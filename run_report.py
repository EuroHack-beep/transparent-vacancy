import argparse
from http.client import HTTPException
import logging
import os
from pathlib import Path

from analyze_vacancies import analyze_vacancy, read_vacancies, write_report
from fetch_vacancies import REGION_39, fetch_vacancies


def main():
    parser = argparse.ArgumentParser(description="Загрузить вакансии и сохранить отчёт редактору.")
    parser.add_argument("--url", default=os.getenv("VACANCY_SOURCE") or REGION_39)
    parser.add_argument("--limit", type=int, default=os.getenv("VACANCY_LIMIT") or "20")
    parser.add_argument("--timeout", type=int, default=os.getenv("VACANCY_TIMEOUT") or "60",
                        help="Тайм-аут одного запроса в секундах.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        vacancies = read_vacancies(fetch_vacancies(args.url, args.limit, args.timeout))
        report = [analyze_vacancy(vacancy) for vacancy in vacancies]
        write_report(args.output, report)
    except (ValueError, OSError, RecursionError, HTTPException) as error:
        saved = f"\nПредыдущий отчёт сохранён: {args.output}" if args.output.is_file() else ""
        parser.exit(1, f"Ошибка формирования отчёта: {error}{saved}\n")
    print(f"Сохранён отчёт по {len(report)} вакансиям: {args.output}")


if __name__ == "__main__":
    main()
