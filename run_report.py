import argparse
from http.client import HTTPException
from pathlib import Path

from analyze_vacancies import analyze_vacancy, read_vacancies, write_report
from fetch_vacancies import REGION_39, fetch_vacancies


def main():
    parser = argparse.ArgumentParser(description="Загрузить вакансии и сохранить отчёт редактору.")
    parser.add_argument("--url", default=REGION_39)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        vacancies = read_vacancies(fetch_vacancies(args.url, args.limit))
        report = [analyze_vacancy(vacancy) for vacancy in vacancies]
        write_report(args.output, report)
    except (ValueError, OSError, RecursionError, HTTPException) as error:
        parser.exit(1, f"Ошибка формирования отчёта: {error}\n")
    print(f"Сохранён отчёт по {len(report)} вакансиям: {args.output}")


if __name__ == "__main__":
    main()
