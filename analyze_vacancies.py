import argparse
from datetime import datetime, timezone
import logging
from pathlib import Path

from report_storage import report_lock, write_report
from validation import load_local, validate_report
from vacancy_checks import check_conditions


def read_page_vacancies(payload):
    if not isinstance(payload, dict):
        raise ValueError("Ответ API должен быть JSON-объектом.")

    status = payload.get("status")
    if status is not None and status not in (200, "200"):
        raise ValueError("Входной JSON содержит ответ API с ошибкой.")

    results = payload.get("results")
    entries = results.get("vacancies") if isinstance(results, dict) else None
    metadata = payload.get("meta")
    if entries is None and isinstance(metadata, dict) and str(metadata.get("total")) == "0":
        return []
    if not isinstance(entries, list):
        raise ValueError("В ответе API ожидается массив results.vacancies.")

    vacancies = []
    for index, entry in enumerate(entries, start=1):
        vacancy = entry.get("vacancy") if isinstance(entry, dict) else None
        if not isinstance(vacancy, dict):
            raise ValueError(f"Запись {index} должна содержать объект vacancy.")

        vacancy_id = vacancy.get("id")
        if not isinstance(vacancy_id, str) or not vacancy_id.strip():
            raise ValueError(f"В записи {index} отсутствует строковый vacancy.id.")
        identifier = vacancy_id.strip()
        if "vac_url" in vacancy and vacancy["vac_url"] is not None and not isinstance(vacancy["vac_url"], str):
            raise ValueError(f"В записи {index} поле vacancy.vac_url должно быть строкой.")
        numbers = {field: vacancy.get(field) for field in ("salary_min", "salary_max", "experience")}
        if isinstance(vacancy.get("requirement"), dict):
            numbers["requirement.experience"] = vacancy["requirement"].get("experience")
        for field, value in numbers.items():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float, str))):
                raise ValueError(f"В записи {index} поле {field} должно быть числом или строкой.")
        vacancies.append(vacancy if identifier == vacancy_id else {**vacancy, "id": identifier})

    return vacancies


def updated_at(vacancy):
    for field in ("date_modify", "updated_at", "lastModified", "modified", "last_modified", "update_date"):
        value = vacancy.get(field)
        if isinstance(value, str):
            try:
                timestamp = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
            except ValueError:
                continue
            if timestamp.tzinfo is not None:
                return timestamp.astimezone(timezone.utc)
    return None


def collect_vacancy(vacancies, vacancy):
    identifier = vacancy["id"]
    previous = vacancies.get(identifier)
    if previous is not None and previous != vacancy:
        old, new = updated_at(previous), updated_at(vacancy)
        if old is not None and new is not None and old != new:
            logging.getLogger(__name__).warning("Найден конфликтующий дубль. Выбрана более свежая запись.")
            if old > new:
                return
        else:
            logging.getLogger(__name__).warning("Найден конфликтующий дубль. Выбрана последняя запись.")
    vacancies[identifier] = vacancy


def read_vacancies(payload):
    pages = payload if isinstance(payload, list) else [payload]
    vacancies = {}
    for page in pages:
        for vacancy in read_page_vacancies(page):
            collect_vacancy(vacancies, vacancy)
    return vacancies.values()


def analyze_vacancy(vacancy):
    report_entry = {"id": vacancy["id"], "issues": check_conditions(vacancy)}
    url = vacancy.get("vac_url")
    if isinstance(url, str) and url.strip():
        report_entry["url"] = url
    return report_entry


def generate_report(vacancies, destination):
    report = [analyze_vacancy(vacancy) for vacancy in vacancies]
    validate_report(report, {vacancy["id"] for vacancy in vacancies})
    write_report(destination, report)
    return len(report)


def main():
    parser = argparse.ArgumentParser(description="Проверка вакансий из локального ответа API.")
    parser.add_argument("input", type=Path, help="Входной JSON в формате API «Работа России»")
    parser.add_argument("--output", type=Path, required=True, help="Путь к отчёту JSON")
    args = parser.parse_args()

    try:
        if args.input.resolve() == args.output.resolve():
            raise ValueError("Входной файл и файл отчёта должны быть разными.")
        with report_lock(args.output) as destination:
            count = generate_report(read_vacancies(load_local(args.input)), destination)
    except (OSError, ValueError, RecursionError) as error:
        parser.exit(1, f"Ошибка: {error}\n")

    print(f"Проверено вакансий: {count}. Отчёт: {args.output}")


if __name__ == "__main__":
    main()
