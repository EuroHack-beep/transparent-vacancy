import argparse
import json
from pathlib import Path
import tempfile

from vacancy_checks import check_conditions


def read_vacancies(payload):
    if not isinstance(payload, dict):
        raise ValueError("Ответ API должен быть JSON-объектом.")

    status = payload.get("status")
    if status is not None and status not in (200, "200"):
        raise ValueError("Входной JSON содержит ответ API с ошибкой.")

    results = payload.get("results")
    if not isinstance(results, dict) or not isinstance(results.get("vacancies"), list):
        raise ValueError("В ответе API ожидается массив results.vacancies.")

    vacancies = {}
    for index, entry in enumerate(results["vacancies"], start=1):
        vacancy = entry.get("vacancy") if isinstance(entry, dict) else None
        if not isinstance(vacancy, dict):
            raise ValueError(f"Запись {index} должна содержать объект vacancy.")

        vacancy_id = vacancy.get("id")
        if not isinstance(vacancy_id, str) or not vacancy_id.strip():
            raise ValueError(f"В записи {index} отсутствует строковый vacancy.id.")
        vacancies[vacancy_id] = vacancy

    return vacancies.values()


def positive_salary(value):
    if isinstance(value, str) and value.strip().isdecimal():
        value = int(value.strip())
    if type(value) is int and value > 0:
        return value
    return None


def analyze_vacancy(vacancy):
    minimum = positive_salary(vacancy.get("salary_min"))
    maximum = positive_salary(vacancy.get("salary_max"))
    issues = check_conditions(vacancy, minimum is not None or maximum is not None)
    if minimum is not None and maximum is not None and minimum > maximum:
        issues.append({
            "topic": "salary",
            "method": "rule",
            "source_fields": ["salary_min", "salary_max"],
            "source_value": {
                "salary_min": vacancy["salary_min"],
                "salary_max": vacancy["salary_max"],
            },
            "problem": "Минимальная зарплата указана выше максимальной.",
            "recommendation": "Уточните нижнюю и верхнюю границы зарплаты и исправьте диапазон.",
        })

    report_entry = {"id": vacancy["id"], "issues": issues}
    url = vacancy.get("vac_url")
    if isinstance(url, str) and url.strip():
        report_entry["url"] = url
    return report_entry


def write_report(output_path, report):
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output_path.parent, delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(report, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        temporary_path.replace(output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Проверка вакансий из локального ответа API.")
    parser.add_argument("input", type=Path, help="Входной JSON в формате API «Работа России»")
    parser.add_argument("--output", type=Path, required=True, help="Путь к отчёту JSON")
    args = parser.parse_args()

    try:
        if args.input.resolve() == args.output.resolve():
            raise ValueError("Входной файл и файл отчёта должны быть разными.")
        with args.input.open(encoding="utf-8-sig") as file:
            vacancies = read_vacancies(json.load(file))
        report = [analyze_vacancy(vacancy) for vacancy in vacancies]
        write_report(args.output, report)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Ошибка: {error}\n")

    print(f"Проверено вакансий: {len(report)}. Отчёт: {args.output}")


if __name__ == "__main__":
    main()
