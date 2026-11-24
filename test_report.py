import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from analyze_vacancies import analyze_vacancy, read_vacancies
from run_report import main as run_live_report
from vacancy_checks import TOPICS


class ReportTests(unittest.TestCase):
    def topics(self, **fields):
        return {issue["topic"]: issue for issue in analyze_vacancy({"id": "test", **fields})["issues"]}

    def test_missing_topics(self):
        issues = self.topics()
        self.assertEqual(set(issues), set(TOPICS))
        self.assertTrue(all(issue["source_value"] == "" for issue in issues.values()))

    def test_schedule_qualified_elsewhere(self):
        vague = "Гибкий график работы"
        issue = self.topics(schedule=vague)["schedule"]
        self.assertEqual(issue["method"], "nearest_neighbor")
        self.assertEqual(issue["source_value"], vague)
        for conditions in ("По будням с 10:00 до 19:00", "Смены 2/2"):
            self.assertNotIn("schedule", self.topics(schedule=vague, conditions=conditions))

    def test_salary_evidence_and_bounds(self):
        text = "У нас конкурентная зарплата"
        issue = self.topics(description=text)["salary"]
        self.assertEqual((issue["method"], issue["source_fields"], issue["source_value"]),
                         ("nearest_neighbor", ["description"], text))
        self.assertNotIn("salary", self.topics(description=text, salary_min=45000))
        self.assertNotIn("salary", self.topics(salary=text, duty="Оклад 45 000 рублей"))
        self.assertEqual(self.topics(salary_min="55000", salary_max=45000)["salary"]["source_value"],
                         {"salary_min": "55000", "salary_max": 45000})

    def test_context_for_short_phrases(self):
        self.assertEqual(self.topics(salary="По договорённости")["salary"]["method"], "nearest_neighbor")
        self.assertEqual(self.topics(schedule="По договорённости")["schedule"]["method"], "nearest_neighbor")
        self.assertIn("schedule", self.topics(schedule="Гибкий график", description="Ставка 45.00 руб."))

    def test_requirements_and_address(self):
        issues = self.topics(requirement={"experience": 0}, addresses={"address": [{"location": "г. Калининград"}]})
        self.assertNotIn("requirements", issues)
        self.assertNotIn("location", issues)
        self.assertIn("location", self.topics(company={"address": "Юридический адрес организации"}))

    def test_narrative_evidence(self):
        description = ("Должность: архивист. Обязанности: принимать документы. Требования: опыт работы от года. "
                       "Оклад 45000 рублей. График 5/2. Полная занятость. Место работы: Калининград.")
        self.assertEqual(self.topics(description=description), {})

    def test_duplicate_ids(self):
        data = {"results": {"vacancies": [{"vacancy": {"id": "a"}}, {"vacancy": {"id": "a", "duty": "Архив"}}]}}
        self.assertEqual(list(read_vacancies(data)), [{"id": "a", "duty": "Архив"}])

    def test_api_failure_keeps_last_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_bytes(b'[{"id":"last-success"}]')
            with patch("sys.argv", ["run_report.py", "--output", str(path)]), \
                 patch("run_report.fetch_vacancies", side_effect=OSError("offline")), \
                 redirect_stderr(StringIO()), self.assertRaises(SystemExit) as stopped:
                run_live_report()
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(path.read_bytes(), b'[{"id":"last-success"}]')


if __name__ == "__main__":
    unittest.main()
