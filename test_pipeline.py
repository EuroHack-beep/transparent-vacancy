import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fetch_vacancies import REGION_39, fetch_vacancies


ROOT = Path(__file__).resolve().parent


def page(ids, total):
    rows = [{"vacancy": {"id": identifier}} for identifier in ids]
    return io.BytesIO(json.dumps({"status": "200", "meta": {"total": total},
                                  "results": {"vacancies": rows}}).encode())


class PipelineTests(unittest.TestCase):
    def test_timeout_retries_same_page(self):
        with patch("fetch_vacancies.urlopen", side_effect=[TimeoutError(), page(["a"], 1)]) as api, \
             patch("fetch_vacancies.sleep"):
            result = fetch_vacancies(REGION_39, 1)
        self.assertEqual(result["meta"]["total"], "1")
        self.assertEqual(api.call_args_list[0].args[0].full_url, api.call_args_list[1].args[0].full_url)

    def test_pagination_with_overlapping_pages(self):
        pages = [page(["a", "b"], 6), page(["b", "c"], 6), page(["d", "e"], 6)]
        with patch("fetch_vacancies.urlopen", side_effect=pages) as api:
            result = fetch_vacancies(REGION_39, 5)
        self.assertEqual([v["vacancy"]["id"] for v in result["results"]["vacancies"]], list("abcde"))
        queries = [parse_qs(urlsplit(call.args[0].full_url).query) for call in api.call_args_list]
        self.assertEqual([q["offset"] for q in queries], [["0"], ["1"], ["2"]])
        self.assertTrue(all(q["limit"] == ["2"] for q in queries))

    def test_short_page_disagrees_with_total(self):
        for ids in ([], ["a"]):
            with self.subTest(ids=ids), patch("fetch_vacancies.urlopen", return_value=page(ids, 20)):
                with self.assertRaises(ValueError):
                    fetch_vacancies(REGION_39, 10)

    def test_genuinely_empty_result(self):
        with patch("fetch_vacancies.urlopen", return_value=page([], 0)):
            self.assertEqual(fetch_vacancies(REGION_39, 10)["results"]["vacancies"], [])

    def test_page_size_limit(self):
        with patch("fetch_vacancies.MAX_PAGE_BYTES", 50), patch("fetch_vacancies.urlopen", return_value=page(["a"], 1)):
            with self.assertRaisesRegex(ValueError, "размер"):
                fetch_vacancies(REGION_39, 1)

    def test_repeated_page_rejected(self):
        with patch("fetch_vacancies.urlopen", side_effect=[page(["a", "b"], 6), page(["a", "b"], 6)]):
            with self.assertRaises(ValueError):
                fetch_vacancies(REGION_39, 5)

    def test_offline_entry_point_and_saved_example(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            command = [sys.executable, str(ROOT / "analyze_vacancies.py"), str(ROOT / "examples/vacancies.json"),
                       "--output", str(output)]
            subprocess.run(command, cwd=directory, check=True, capture_output=True)
            first = output.read_bytes()
            expected = json.loads((ROOT / "examples/report.json").read_bytes())
            self.assertEqual(json.loads(first), expected)
            subprocess.run(command, cwd=directory, check=True, capture_output=True)
            self.assertEqual(output.read_bytes(), first)
            invalid = Path(directory) / "invalid.json"
            invalid.write_text('{"results": {}}', encoding="utf-8")
            command[2] = str(invalid)
            self.assertNotEqual(subprocess.run(command, cwd=directory, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), first)
