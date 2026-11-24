import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest

from analyze_vacancies import analyze_vacancy, read_vacancies
from compare_reports import compare_reports
from test_pipeline import ROOT, fixture_portal
from vacancy_checks import check_conditions


def issue(*, fields=("schedule", "description"), value="2/2", question="Какой график?"):
    return {"topic": "schedule", "source_fields": list(fields), "source_value": value,
            "problem": "Несогласованный график", "recommendation": question, "method": "rule"}


class EditorTests(unittest.TestCase):
    def test_editor_demo_removes_only_the_schedule_issue(self):
        def analyze(path):
            cards = read_vacancies(json.loads(path.read_bytes()))
            return [analyze_vacancy(card) for card in cards]

        before = analyze(ROOT / "examples/vacancies.json")
        after = analyze(ROOT / "examples/vacancies-corrected.json")
        result = compare_reports(before, after)
        self.assertEqual(result["summary"], {
            "compared_vacancies": 4, "added_vacancies": 0, "removed_vacancies": 0,
            "resolved": 1, "added": 0, "changed": 0, "unchanged": 17,
        })
        self.assertEqual(result["vacancies"][2]["resolved"][0]["topic"], "schedule")

    def test_missing_card_is_not_counted_as_resolved_and_repeated_issues_are_deduplicated(self):
        old = [{"id": "gone", "issues": [issue()]},
               {"id": "same", "issues": [issue(), issue()]}]
        new = [{"id": "added-card", "issues": [issue()]},
               {"id": "same", "issues": [issue(fields=("description", "schedule"))]}]
        result = compare_reports(old, new)
        self.assertEqual(result["removed_vacancies"], ["gone"])
        self.assertEqual(result["added_vacancies"], ["added-card"])
        self.assertEqual(result["summary"]["resolved"], 0)
        self.assertEqual(result["summary"]["added"], 0)
        self.assertEqual(result["summary"]["unchanged"], 1)

    def test_changed_evidence_and_independent_issues(self):
        before = [{"id": "case", "issues": [issue()]}]
        after = [{"id": "case", "issues": [issue(value="5/2"), issue(fields=("workingHours",))]}]
        result = compare_reports(before, after)
        diff = result["vacancies"][0]
        self.assertEqual(diff["changed"][0]["after"]["source_value"], "5/2")
        self.assertEqual(diff["added"][0]["source_fields"], ["workingHours"])
        self.assertFalse(diff["resolved"])

    def test_invalid_report_preserves_existing_comparison(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            malformed, output = root / "bad.json", root / "comparison.json"
            malformed.write_bytes(b'[{"id":"a","issues":[3]}]')
            output.write_bytes(b'{"last_success":true}\n')
            command = [sys.executable, str(ROOT / "compare_reports.py"), str(ROOT / "examples/report.json")]
            result = subprocess.run([*command, str(malformed), "--output", str(output)],
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn("некорректное замечание", result.stderr)
            self.assertEqual(output.read_bytes(), b'{"last_success":true}\n')
            result = subprocess.run([*command, str(malformed), "--output", str(malformed)],
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(malformed.read_bytes(), b'[{"id":"a","issues":[3]}]')


class PortalReviewRegressionTests(unittest.TestCase):
    def topics(self, **values):
        return {item["topic"]: item for item in check_conditions(values)}

    def test_frequency_and_mixed_letters_do_not_hide_a_known_schedule(self):
        description = ("Уcловия: Грaфик рaботы cменный 1/3 "
                       "Заработная плата 2 раза в месяц без задержек.")
        issues = self.topics(salary_min=65000, salary_max=65000,
                             schedule="Сменная работа", description=description)
        self.assertFalse({"schedule", "salary"} & set(issues))

    def test_experience_in_months_compares_with_structured_years(self):
        needed = "Опыт работы в такой должности от 6-ти месяцев; профильное образование будет преимуществом."
        issue = self.topics(requirement={"experience": 1}, requirements=needed)["requirements"]
        self.assertEqual(issue["problem"], "Указаны разные значения минимального стажа.")
        self.assertEqual(issue["source_value"]["requirement.experience"], 1)
        self.assertEqual(issue["source_value"]["requirements"], needed)
        self.assertNotIn("requirements", self.topics(requirement={"experience": 0.5}, requirements=needed))
        self.assertNotIn("requirements", self.topics(
            requirement={"experience": 0}, requirements="Опыт от 6 месяцев приветствуется."))

    def test_named_teaching_task_and_generic_long_term_activity_are_treated_independently(self):
        issues = self.topics(duty='Согласно должностной инструкции "Учитель". '
                                  'Постоянная работа с учащимися, проводит уроки и проверяет задания.')
        self.assertNotIn("duties", issues)
        self.assertIn("employment", issues)

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL required for local TLS test")
    def test_real_tls_exchange_and_rejection_of_untrusted_certificate(self):
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            certificate, key = root / "cert.pem", root / "key.pem"
            subprocess.run(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                 "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=localhost",
                 "-addext", "subjectAltName=IP:127.0.0.1"],
                check=True, capture_output=True, timeout=10,
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, key)
            with fixture_portal(context) as (url, requests):
                report = root / "latest.json"
                env = {**os.environ, "VACANCY_PRIVATE_ORIGINS": url, "VACANCY_ALLOW_HTTP": "0"}
                command = [sys.executable, str(ROOT / "run_report.py"), "--url", url + "/region/39",
                           "--limit", "4", "--timeout", "3", "--output", str(report)]
                trusted = subprocess.run(command, env={**env, "SSL_CERT_FILE": str(certificate)},
                                         capture_output=True, text=True, timeout=10)
                self.assertEqual(trusted.returncode, 0, trusted.stderr)
                original = report.read_bytes()
                self.assertEqual(original, (ROOT / "examples/report.json").read_bytes())
                self.assertEqual(requests, [(0, 2), (1, 2)])
                requests.clear()
                env.pop("SSL_CERT_FILE", None)
                untrusted = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(untrusted.returncode, 1)
                self.assertIn("TLS", untrusted.stderr)
                self.assertEqual(requests, [])
                self.assertEqual(report.read_bytes(), original)
