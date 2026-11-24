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

    def test_unrelated_numbers(self):
        issues = self.topics(salary="Выплаты с 2026 года", description="Собеседование в 10:00")
        self.assertIn("salary", issues)
        self.assertIn("schedule", issues)
        self.assertNotIn("salary", self.topics(salary="45 000 – 55 000"))

    def test_location_and_placeholders(self):
        self.assertIn("location", self.topics(description="Удаленная работа невозможна"))
        self.assertNotIn("location", self.topics(description="Работа в Калининграде"))
        self.assertEqual(self.topics(employment="Уточняется")["employment"]["source_value"], "")
        for word in ("Удаленная", "Удалённая"):
            self.assertNotIn("location", self.topics(description=f"{word} работа"))

    def test_duplicate_ids(self):
        data = {"results": {"vacancies": [{"vacancy": {"id": "a"}}, {"vacancy": {"id": "a", "duty": "Архив"}}]}}
        self.assertEqual(list(read_vacancies(data)), [{"id": "a", "duty": "Архив"}])

    def test_contradictions_have_both_sources(self):
        cases = (
            ("job_title", {"job-name": "Архивист", "description": "Должность: кладовщик."},
             {"job-name", "description"}),
            ("requirements", {"requirement": {"qualification": "Можно без опыта"},
                              "description": "Требования: опыт работы от 2 лет."},
             {"requirement.qualification", "description"}),
            ("salary", {"salary_min": 45000, "salary_max": "55 000,00",
                        "description": "Оклад: 70 тыс. рублей в месяц."},
             {"salary_min", "salary_max", "description"}),
            ("schedule", {"schedule": "График 2/2", "description": "График 5/2 с 09:00 до 18:00."},
             {"schedule", "description"}),
            ("location", {"workLocation": "Калининград, ул. Мира, 10",
                          "description": "Место работы: Советск, ул. Ленина, 10."},
             {"workLocation", "description"}),
            ("location", {"workLocation": "Калининград, ул. Мира, 10",
                          "description": "Место работы: Калининград, улица Мира, 15."},
             {"workLocation", "description"}),
            ("employment", {"employment": "Полная занятость",
                            "description": "Тип занятости: частичная занятость."},
             {"employment", "description"}),
        )
        for topic, fields, sources in cases:
            with self.subTest(topic=topic, fields=fields):
                issue = self.topics(**fields)[topic]
                self.assertEqual(issue["method"], "rule")
                self.assertEqual(set(issue["source_fields"]), sources)
                self.assertEqual(set(issue["source_value"]), sources)

    def test_conflicting_clauses_in_same_field_keep_both_quotes(self):
        issue = self.topics(description="График: 5/2. График: 2/2.")["schedule"]
        self.assertEqual(issue["source_fields"], ["description"])
        self.assertEqual(issue["source_value"], [
            {"field": "description", "value": "5/2."}, {"field": "description", "value": "2/2."},
        ])

    def test_vague_structured_fields_are_not_treated_as_specific(self):
        fields = {"job-name": "Специалист", "duty": "Выполнение поручений руководителя",
                  "requirement": {"qualification": "Ответственность"},
                  "salary": "Согласно штатному расписанию",
                  "schedule": "Посменный график по производственной необходимости",
                  "workLocation": "На территории работодателя", "employment": "Условия обсуждаются"}
        issues = self.topics(**fields)
        self.assertEqual(set(issues), set(TOPICS))
        self.assertTrue(all(issue["source_value"] for issue in issues.values()))

    def test_model_adds_issue_on_paraphrase(self):
        text = "Решение текущих задач руководства отдела"
        issue = self.topics(duty=text)["duties"]
        self.assertEqual(issue["method"], "nearest_neighbor")
        self.assertEqual(issue["source_value"], text)
        with patch("vacancy_checks.classify", return_value=None):
            self.assertNotIn("duties", self.topics(duty=text))

    def test_specifics_disambiguate_other_fields_and_html_blocks(self):
        issues = self.topics(
            duty="Выполнение поручений руководителя и обслуживание рабочих станций",
            schedule="Гибкий график",
            salary="По договорённости",
            description="<p>График: по будням с 09:00 до 18:00.</p><p>Оклад: 50–60 тыс. ₽.</p>",
            workLocation="На территории работодателя",
            conditions="Работа полностью удалённая.",
        )
        self.assertFalse({"duties", "schedule", "salary", "location"} & set(issues))

    def test_compatible_alternatives_and_compensation_basis(self):
        issues = self.topics(
            schedule="График 2/2 или 5/2",
            salary_min=55000, salary_max=55000,
            employment="Полная занятость",
            description="График: 5/2. Ставка: 2500 рублей за смену. "
                        "Зарплата: 48 000 рублей на руки. Занятость: временная.",
        )
        self.assertFalse({"schedule", "salary", "employment"} & set(issues))

    def test_several_offices_are_options(self):
        issues = self.topics(
            addresses={"address": [{"location": "Калининград, ул. Мира, 10"},
                                   {"location": "Советск, ул. Ленина, 10"}]},
            description="Место работы: Советск, улица Ленина, 10.",
        )
        self.assertNotIn("location", issues)

    def test_recruitment_logistics_do_not_override_work_conditions(self):
        issues = self.topics(workLocation="Калининград, ул. Мира, 10", schedule="Гибкий график",
                            description="Собеседование в Советске, ул. Ленина, 6. Дата собеседования 12/12.")
        self.assertNotIn("location", issues)
        self.assertEqual(issues["schedule"]["source_value"], "Гибкий график")
        missing = self.topics(description="Собеседование в офисе на ул. Ленина, 6.")
        self.assertEqual(missing["location"]["source_value"], "")

    def test_work_city_in_prose_and_address_with_slash(self):
        self.assertNotIn("location", self.topics(description="Работа в Калининграде."))
        issues = self.topics(schedule="По договоренности", description="Адрес: Калининград, улица Мира, 2/2.")
        self.assertEqual(issues["schedule"]["source_value"], "По договоренности")

    def test_pay_per_shift_bonus_only_and_unpaid_internship(self):
        self.assertNotIn("salary", self.topics(salary="3 500 ₽/смена"))
        bonus = "Приветственный бонус 5 000 рублей."
        self.assertEqual(self.topics(conditions=bonus)["salary"]["source_value"], bonus)
        unpaid = self.topics(description="Неоплачиваемая стажировка.")
        self.assertNotIn("salary", unpaid)
        self.assertNotIn("employment", unpaid)
        self.assertEqual(unpaid["requirements"]["source_value"], "")

    def test_conflict_only_for_schedule_outside_explicit_choices(self):
        okay = self.topics(schedule="5/2", description="График на выбор: 5/2; 2/2.")
        self.assertNotIn("schedule", okay)
        different = self.topics(schedule="2/2 или 5/2", description="График: 3/3.")
        self.assertEqual(different["schedule"]["source_value"],
                         {"schedule": "2/2 или 5/2", "description": "3/3."})

    def test_fractional_api_experience_detects_conflict(self):
        issues = self.topics(requirement={"experience": 2.0}, description="Требования: без опыта.")
        self.assertEqual(issues["requirements"]["source_value"],
                         {"requirement.experience": 2.0, "description": "без опыта."})

    def test_unqualified_experience_and_reference_to_internal_rules(self):
        vague = self.topics(requirement={"qualification": "Необходим опыт работы."},
                           duty="Выполнение работ согласно регламенту")
        self.assertEqual(vague["requirements"]["source_value"], "Необходим опыт работы.")
        self.assertEqual(vague["duties"]["source_value"], "Выполнение работ согласно регламенту")
        clear = self.topics(requirement={"qualification": "Необходим опыт работы от 2 лет."},
                           duty="Обслуживать рабочие станции согласно регламенту")
        self.assertFalse({"requirements", "duties"} & set(clear))

    def test_locality_abbreviation_and_work_from_home(self):
        for description in ("Место работы — пос. Рыбачий.", "Место работы: Пос. Рыбачий.",
                            "Полностью работаем из дома."):
            with self.subTest(description=description):
                self.assertNotIn("location", self.topics(description=description))
        remote_office = self.topics(workLocation="Работа из дома", description="Формат работы: в офисе.")
        self.assertEqual(remote_office["location"]["source_fields"], ["workLocation", "description"])

    def test_known_shift_duration_does_not_define_days(self):
        fields = {"schedule": "Скользящий график", "description": "Смена длится 12 часов, дни согласуются."}
        remark = self.topics(**fields)["schedule"]
        self.assertEqual(remark["source_value"], "Скользящий график")
        self.assertIn("В какие дни", remark["recommendation"])
        self.assertNotIn("schedule", self.topics(**(fields | {"conditions": "Смены 2/2."})))

    def test_ordinal_payment_and_wages_for_distinct_periods(self):
        vague = "Оплата по договоренности, предусмотрена 13-я зарплата."
        self.assertEqual(self.topics(salary=vague)["salary"]["source_value"], vague)
        prose = "Неоплачиваемая стажировка. После стажировки оклад 60 000 рублей в месяц."
        self.assertNotIn("salary", self.topics(description=prose))
        quote = "Первые 3 месяца оклад 30 000 рублей в месяц."
        self.assertNotIn("salary", self.topics(salary_min=30000, salary_max=30000, description=quote))

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
