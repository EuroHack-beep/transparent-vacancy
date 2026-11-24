import argparse
import logging
import ssl
from http.client import HTTPException
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request

from analyze_vacancies import collect_vacancy, read_page_vacancies
from report_storage import report_lock, write_report
from source_policy import open_source as urlopen
from source_policy import source_settings, validate_source
from validation import load_json, positive_setting


REGION_39 = "https://opendata.trudvsem.ru/api/v1/vacancies/region/39"
REGION_39_HTTP = "http://opendata.trudvsem.ru/api/v1/vacancies/region/39"
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_FETCH_BYTES = 64 * 1024 * 1024
FETCH_SECONDS = 300
LOG = logging.getLogger(__name__)


def read_page(url, timeout, deadline, settings):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "transparent-vacancy/1.0"})
    for attempt in range(2):
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ValueError("Истекло время загрузки вакансий.")
            content = bytearray()
            with urlopen(request, timeout=min(timeout, remaining), settings=settings, deadline=deadline) as response:
                while True:
                    if monotonic() >= deadline:
                        raise ValueError("Истекло время загрузки вакансий.")
                    chunk = response.read1(min(65536, MAX_PAGE_BYTES + 1 - len(content)))
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > MAX_PAGE_BYTES:
                        raise ValueError("Страница API превышает допустимый размер.")
            return load_json(content), len(content)
        except HTTPError as error:
            if attempt or error.code not in (408, 429, 500, 502, 503, 504):
                raise
            LOG.warning("Запрос завершился HTTP %d. Повторяю.", error.code)
            error.close()
        except (TimeoutError, ConnectionError, HTTPException, URLError) as error:
            if isinstance(getattr(error, "reason", error), ssl.SSLError):
                raise ValueError("Не удалось установить подтверждённое TLS-соединение.") from None
            if attempt:
                raise
            LOG.warning("Сбой соединения. Повторяю запрос.")
        sleep(max(0, min(1, deadline - monotonic())))


def fetch_vacancies(url, limit, timeout=60):
    if limit < 1:
        raise ValueError("Лимит должен быть положительным.")
    if timeout < 1:
        raise ValueError("Тайм-аут должен быть положительным.")
    settings = source_settings()
    source, _ = validate_source(url, settings)
    deadline = monotonic() + positive_setting("VACANCY_DEADLINE", FETCH_SECONDS, 900)
    max_pages = positive_setting("VACANCY_MAX_PAGES", 100, 2000)
    return fetch_pages(source, limit, timeout, deadline, settings, max_pages)


def fetch_pages(source, limit, timeout, deadline, settings, max_pages):
    filters = [(k, v) for k, v in parse_qsl(source.query, keep_blank_values=True)
               if k not in ("offset", "limit")]
    page_size = min(10, max(1, limit // 2))
    offset = 0
    collected = {}
    expected = None
    previous_total = None
    received = 0
    while len(collected) < limit:
        if offset >= max_pages:
            raise ValueError("Превышен лимит страниц API. Отчёт не обновлён.")
        query = urlencode(filters + [("offset", offset), ("limit", page_size)])
        page_url = urlunsplit(source._replace(query=query, fragment=""))
        try:
            page, size = read_page(page_url, timeout, deadline, settings)
            received += size
            if received > MAX_FETCH_BYTES:
                raise ValueError("Выдача API превышает допустимый суммарный размер.")
            records = read_page_vacancies(page)
        except (ValueError, OSError, RecursionError, HTTPException) as error:
            if isinstance(error, HTTPError):
                detail = f"HTTP {error.code}"
                error.close()
            elif isinstance(error, OSError) or isinstance(error, HTTPException):
                detail = "сетевая ошибка или тайм-аут"
            else:
                detail = str(error)
            raise ValueError(f"Не удалось получить страницу offset={offset}: {detail}") from None
        LOG.info("API offset=%d, limit=%d, записей=%d", offset, page_size, len(records))
        metadata = page.get("meta")
        total = metadata.get("total") if isinstance(metadata, dict) else None
        if total is not None:
            if not str(total).isascii() or not str(total).isdecimal() or len(str(total)) > 12:
                raise ValueError(f"Некорректное meta.total на странице offset={offset}.")
            total = int(total)
            if total < len(records) or (previous_total is not None and total < previous_total):
                raise ValueError(f"Неожиданное сокращение выдачи на странице offset={offset}.")
            if expected is None:
                expected = min(limit, total)
            previous_total = total
        previous_count = len(collected)
        for record in records:
            collect_vacancy(collected, record)
            if len(collected) == limit:
                break
        if records and len(collected) == previous_count:
            raise ValueError(f"В выдаче offset={offset} нет новых вакансий.")
        if expected is not None and len(collected) >= expected:
            break
        if not records:
            if expected is not None:
                raise ValueError(f"Страница offset={offset} оказалась пустой до конца выдачи.")
            break
        if expected is None and len(records) < page_size:
            break
        offset += 1

    return {
        "status": "200",
        "request": {"api": "v1"},
        "meta": {"total": str(len(collected))},
        "results": {"vacancies": [{"vacancy": v} for v in collected.values()]},
    }


def main():
    parser = argparse.ArgumentParser(description="Загрузка вакансий с портала «Работа России».")
    parser.add_argument("--url", default=REGION_39, help="Адрес API региона")
    parser.add_argument("--limit", type=int, default=20, help="Максимальное число вакансий")
    parser.add_argument("--output", type=Path, required=True, help="Путь для сохранения JSON")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        with report_lock(args.output) as destination:
            payload = fetch_vacancies(args.url, args.limit)
            write_report(destination, payload)
    except (ValueError, OSError, RecursionError, HTTPException) as error:
        parser.exit(1, f"Ошибка загрузки: {error}\n")
    print(f"Сохранено вакансий: {payload['meta']['total']}. Файл: {args.output}")


if __name__ == "__main__":
    main()
