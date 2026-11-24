import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from analyze_vacancies import read_vacancies
from report_storage import report_lock, write_report
from source_policy import open_source, source_settings, validate_source
from test_pipeline import ROOT
from vacancy_checks import check_conditions


class HardeningTests(unittest.TestCase):
    def test_allowed_origin_does_not_authorize_http_userinfo_ports_or_private_dns(self):
        with patch.dict(os.environ, {"VACANCY_ALLOWED_ORIGINS": "https://vacancies.example",
                                     "VACANCY_PRIVATE_ORIGINS": "", "VACANCY_ALLOW_HTTP": ""}):
            policy = source_settings()
            for rejected in ("https://vacancies.example:444/path", "https://a@vacancies.example/path",
                             "http://opendata.trudvsem.ru/api", "https://127.0.0.1/api"):
                with self.subTest(url=rejected), self.assertRaises(ValueError):
                    validate_source(rejected, policy)
            dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
            with patch("source_policy.socket.getaddrinfo", return_value=dns), \
                 patch("source_policy.socket.socket") as connection, \
                 self.assertRaisesRegex(ValueError, "DNS"):
                open_source(Request("https://vacancies.example/path"), timeout=2,
                            settings=policy, deadline=float("inf"))
            connection.assert_not_called()

    def test_input_limits_and_failed_write_preserve_sha(self):
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / "in.json"
            report = Path(work) / "out.json"
            report.write_bytes(b'[]\n')
            previous_hash = hashlib.sha256(report.read_bytes()).digest()
            command = [sys.executable, str(ROOT / "analyze_vacancies.py"), str(source), "--output", str(report)]
            excessive = json.dumps({"results": {"vacancies": [{"vacancy": {
                "id": "a", "description": ";".join(f"Зарплата {1000 + i} руб." for i in range(70)),
            }}]}}, ensure_ascii=False).encode()
            cases = [
                (b'{"status":"200","status":"200"}', {}, "повторяющийся ключ"),
                (b'{"unknown":NaN}', {}, "бесконечное значение"),
                (b'{"unknown":1e999}', {}, "бесконечное значение"),
                (b'{"unknown":"excessive"}', {"VACANCY_LOCAL_BYTES": "8"}, "размер"),
                (b'{"unknown":"excessive"}', {"VACANCY_FIELD_CHARS": "8"}, "длина поля"),
                (b'[[[[[]]]]]', {"VACANCY_JSON_DEPTH": "4"}, "глубина"),
                (b'{"results":{"vacancies":[{"vacancy":{"id":true}}]}}', {}, "vacancy.id"),
                (excessive, {}, "бюджет фрагментов"),
                (b'{"unknown":', {}, "Ошибка"),
            ]
            for data, overrides, message in cases:
                with self.subTest(message=message):
                    source.write_bytes(data)
                    result = subprocess.run(command, env={**os.environ, **overrides},
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(message, result.stderr)
                    self.assertEqual(hashlib.sha256(report.read_bytes()).digest(), previous_hash)
            with patch("report_storage.os.fsync", side_effect=OSError("disk error")), self.assertRaises(OSError):
                write_report(report, [{"id": "new", "issues": []}])
            self.assertEqual(hashlib.sha256(report.read_bytes()).digest(), previous_hash)

    def test_competing_process_exits_without_overwriting(self):
        with tempfile.TemporaryDirectory() as work:
            report = Path(work) / "report.json"
            report.write_bytes(b'[]\n')
            original = hashlib.sha256(report.read_bytes()).digest()
            with report_lock(report):
                result = subprocess.run(
                    [sys.executable, str(ROOT / "analyze_vacancies.py"),
                     str(ROOT / "examples/vacancies.json"), "--output", str(report)],
                    capture_output=True, text=True, timeout=10,
                )
            self.assertEqual(result.returncode, 1)
            self.assertIn("другим процессом", result.stderr)
            self.assertEqual(hashlib.sha256(report.read_bytes()).digest(), original)

    def test_conflicting_duplicate_prefers_aware_revision_and_normalizes_id(self):
        data = {"results": {"vacancies": [
            {"vacancy": {"id": " a ", "date_modify": "2026-11-24T13:30:00+0300", "title": "latest"}},
            {"vacancy": {"id": "a", "date_modify": "2026-11-24T10:00:00Z", "title": "older"}},
        ]}}
        with self.assertLogs("analyze_vacancies", level="WARNING"):
            result = list(read_vacancies(data))
        self.assertEqual([(row["id"], row["title"]) for row in result], [("a", "latest")])

    def test_synonym_flexible_start_and_introductory_location_are_compatible(self):
        issues = check_conditions({
            "job-name": "Программист",
            "schedule": "График 5/2 с 9:00 до 18:00",
            "workLocation": "Удалённая работа",
            "description": "Должность: разработчик ПО. График: 5/2, гибкое начало с 08:00 до 10:00. "
                           "Место работы: Калининград, стажировка в офисе.",
        })
        self.assertFalse({"job_title", "schedule", "location"} & {i["topic"] for i in issues})

    def test_independent_conflicts_and_original_markup(self):
        description = "График: 5/2. График: с <b>10:00</b> до 19:00."
        issues = check_conditions({
            "schedule": "График 2/2 с 09:00 до 18:00",
            "description": description,
        })
        matches = [issue for issue in issues if issue["topic"] == "schedule"]
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[1]["source_value"]["description"], description)

    def test_compensation_phases_supplements_and_tax_bases_are_separate(self):
        issues = check_conditions({
            "salary_min": 60000,
            "salary_max": 60000,
            "description": "На испытательном сроке оклад 40 000 рублей. "
                           "После испытательного срока оклад 60 000 рублей. Премия 10 000 рублей.",
            "conditions": "Зарплата 52 000 рублей на руки.",
        })
        self.assertNotIn("salary", {issue["topic"] for issue in issues})
