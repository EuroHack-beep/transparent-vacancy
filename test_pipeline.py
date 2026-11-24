import io
import hashlib
import json
import os
import ssl
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.error import URLError

from fetch_vacancies import REGION_39, fetch_vacancies


ROOT = Path(__file__).resolve().parent


def page(ids, total):
    rows = [{"vacancy": {"id": identifier}} for identifier in ids]
    return io.BytesIO(json.dumps({"status": "200", "meta": {"total": total},
                                  "results": {"vacancies": rows}}).encode())


@contextmanager
def fixture_portal(tls_context=None):
    payload = json.loads((ROOT / "examples/vacancies.json").read_text(encoding="utf-8"))
    rows = payload["results"]["vacancies"]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            offset, limit = int(query["offset"][0]), int(query["limit"][0])
            requests.append((offset, limit))
            if parsed.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/region/39")
                self.end_headers()
                return
            if parsed.path == "/failure" and offset == 1:
                self.send_error(503)
                return
            start = offset * limit
            result = {**payload, "results": {"vacancies": rows[start:start + limit]}}
            data = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        scheme = "http"
        if tls_context is not None:
            server.socket = tls_context.wrap_socket(server.socket, server_side=True)
            scheme = "https"
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield f"{scheme}://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join()


class PipelineTests(unittest.TestCase):
    def test_timeout_retries_same_page(self):
        with patch("fetch_vacancies.urlopen", side_effect=[TimeoutError(), page(["a"], 1)]) as api, \
             patch("fetch_vacancies.sleep"):
            result = fetch_vacancies(REGION_39, 1)
        self.assertEqual(result["meta"]["total"], "1")
        self.assertEqual(api.call_args_list[0].args[0].full_url, api.call_args_list[1].args[0].full_url)

    def test_default_https_never_falls_back_after_second_page_error(self):
        exchanges = [page(["https-a"], 2), TimeoutError(), TimeoutError()]
        with patch("fetch_vacancies.urlopen", side_effect=exchanges) as api, patch("fetch_vacancies.sleep"), \
             self.assertLogs("fetch_vacancies", level="WARNING"):
            with self.assertRaisesRegex(ValueError, "offset=1"):
                fetch_vacancies(REGION_39, 2)
        requests = [urlsplit(call.args[0].full_url) for call in api.call_args_list]
        self.assertEqual([(url.scheme, parse_qs(url.query)["offset"][0]) for url in requests],
                         [("https", "0"), ("https", "1"), ("https", "1")])

    def test_custom_https_source_never_falls_back_to_http(self):
        with patch.dict(os.environ, {"VACANCY_ALLOWED_ORIGINS": "https://example.org"}), \
             patch("fetch_vacancies.urlopen", side_effect=TimeoutError()) as api, \
             patch("fetch_vacancies.sleep"), self.assertLogs("fetch_vacancies", level="WARNING"):
            with self.assertRaisesRegex(ValueError, "offset=0"):
                fetch_vacancies("https://example.org/source", 2)
        self.assertEqual(api.call_count, 2)
        self.assertTrue(all(call.args[0].full_url.startswith("https:") for call in api.call_args_list))

    def test_invalid_tls_certificate_never_switches_protocol(self):
        with patch("fetch_vacancies.urlopen", side_effect=URLError(ssl.SSLCertVerificationError())) as api, \
             patch("fetch_vacancies.sleep") as retry:
            with self.assertRaisesRegex(ValueError, "offset=0"):
                fetch_vacancies(REGION_39, 2)
        self.assertEqual(api.call_count, 1)
        retry.assert_not_called()
        self.assertTrue(all(call.args[0].full_url.startswith("https:") for call in api.call_args_list))

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
            empty_offset = int(bool(ids))
            pages = [page(ids, 20), page([], 20)]
            with self.subTest(ids=ids), patch("fetch_vacancies.urlopen", side_effect=pages):
                with self.assertRaisesRegex(ValueError, f"offset={empty_offset}"):
                    fetch_vacancies(REGION_39, 10)

    def test_genuinely_empty_result(self):
        with patch("fetch_vacancies.urlopen", return_value=page([], 0)):
            self.assertEqual(fetch_vacancies(REGION_39, 10)["results"]["vacancies"], [])

    def test_page_size_limit(self):
        with patch("fetch_vacancies.MAX_PAGE_BYTES", 50), patch("fetch_vacancies.urlopen", return_value=page(["a"], 1)):
            with self.assertRaisesRegex(ValueError, "размер"):
                fetch_vacancies(REGION_39, 1)

    def test_fetch_deadline_stops_before_request(self):
        with patch("fetch_vacancies.monotonic", side_effect=(0, 301)), patch("fetch_vacancies.urlopen") as api:
            with self.assertRaisesRegex(ValueError, "Истекло время"):
                fetch_vacancies(REGION_39, 1)
        api.assert_not_called()

    def test_repeated_page_rejected(self):
        with patch("fetch_vacancies.urlopen", side_effect=[page(["a", "b"], 6), page(["b", "a"], 6)]):
            with self.assertRaises(ValueError):
                fetch_vacancies(REGION_39, 5)

    def test_total_shrink_and_page_budget_reject_partial_reports(self):
        with patch("fetch_vacancies.urlopen", side_effect=[page(["a", "b"], 5), page(["c"], 3)]):
            with self.assertRaisesRegex(ValueError, "сокращение"):
                fetch_vacancies(REGION_39, 4)
        with patch.dict(os.environ, {"VACANCY_MAX_PAGES": "1"}), \
             patch("fetch_vacancies.urlopen", return_value=page(["a", "b"], 5)) as api:
            with self.assertRaisesRegex(ValueError, "лимит страниц"):
                fetch_vacancies(REGION_39, 4)
            self.assertEqual(api.call_count, 1)

    def test_short_page_with_more_results_and_unexpected_empty_page(self):
        with patch("fetch_vacancies.urlopen", side_effect=[page(["a"], 2), page(["b"], 2)]):
            self.assertEqual(fetch_vacancies(REGION_39, 4)["meta"]["total"], "2")
        with patch("fetch_vacancies.urlopen", side_effect=[page(["a"], 2), page([], 2)]):
            with self.assertRaisesRegex(ValueError, "offset=1"):
                fetch_vacancies(REGION_39, 4)

    def test_real_http_process_preserves_report_after_failed_refresh(self):
        with fixture_portal() as (url, requests), tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reports" / "latest.json"
            command = [sys.executable, str(ROOT / "run_report.py"), "--output", str(output)]
            env = {**os.environ, "VACANCY_LIMIT": "4", "VACANCY_SOURCE": url + "/region/39",
                   "VACANCY_TIMEOUT": "2", "VACANCY_ALLOW_HTTP": "1", "VACANCY_PRIVATE_ORIGINS": url}
            for _ in range(2):
                requests.clear()
                subprocess.run(command, env=env, check=True, capture_output=True, timeout=10)
                self.assertEqual(output.read_bytes(), (ROOT / "examples/report.json").read_bytes())
                self.assertEqual(requests, [(0, 2), (1, 2)])
            requests.clear()
            original = hashlib.sha256(output.read_bytes()).digest()
            failure = subprocess.run(command + ["--url", url + "/failure"], env=env,
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(failure.returncode, 1)
            self.assertIn("offset=1", failure.stderr)
            self.assertIn("Предыдущий отчёт сохранён", failure.stderr)
            self.assertEqual(requests, [(0, 2), (1, 2), (1, 2)])
            self.assertEqual(hashlib.sha256(output.read_bytes()).digest(), original)
            requests.clear()
            redirected = subprocess.run(command + ["--url", url + "/redirect"], env=env,
                                        capture_output=True, text=True, timeout=10)
            self.assertEqual(redirected.returncode, 1)
            self.assertIn("Перенаправление", redirected.stderr)
            self.assertEqual(requests, [(0, 2)])
            self.assertEqual(hashlib.sha256(output.read_bytes()).digest(), original)

    def test_offline_multiple_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            payload = Path(directory) / "pages.json"
            payload.write_text(json.dumps([json.load(page(["a", "b"], 3)),
                                           json.load(page(["b", "c"], 3))]), encoding="utf-8")
            command = [sys.executable, str(ROOT / "analyze_vacancies.py"), str(payload),
                       "--output", str(output)]
            subprocess.run(command, check=True, capture_output=True, timeout=10)
            self.assertEqual([row["id"] for row in json.loads(output.read_bytes())], ["a", "b", "c"])

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
