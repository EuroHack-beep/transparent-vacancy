FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY analyze_vacancies.py clarity_model.py fetch_vacancies.py run_report.py vacancy_checks.py vacancy_facts.py ./
COPY report_storage.py source_policy.py validation.py ./
COPY compare_reports.py ./
COPY datasets/baseline.jsonl datasets/baseline.jsonl
COPY examples/vacancies.json examples/vacancies.json
COPY examples/vacancies-corrected.json examples/vacancies-corrected.json

RUN mkdir /app/reports /input && chown 1000:1000 /app/reports

USER 1000:1000
CMD ["python", "analyze_vacancies.py", "examples/vacancies.json", "--output", "reports/sample-report.json"]
