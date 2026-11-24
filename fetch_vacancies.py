import argparse
import json
import logging
import ssl
from http.client import HTTPException
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from analyze_vacancies import read_page_vacancies, write_report


REGION_39 = "https://opendata.trudvsem.ru/api/v1/vacancies/region/39"
REGION_39_HTTP = "http://opendata.trudvsem.ru/api/v1/vacancies/region/39"
MAX_PAGE_BYTES = 8 * 1024 * 1024
FETCH_SECONDS = 300
LOG = logging.getLogger(__name__)


class SourceUnavailable(ValueError):
    """The source could not return a page because the connection failed."""


def read_page(url, timeout, deadline):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "transparent-vacancy/1.0"})
    for attempt in range(2):
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ValueError("Истекло время загрузки вакансий.")
            content = bytearray()
            with urlopen(request, timeout=min(timeout, remaining)) as response:
                while True:
                    if monotonic() >= deadline:
                        raise ValueError("Истекло время загрузки вакансий.")
                    chunk = response.read1(min(65536, MAX_PAGE_BYTES + 1 - len(content)))
                    if not chunk:
                        break
                    content.extend(chunk)
                    if len(content) > MAX_PAGE_BYTES:
                        raise ValueError("Страница API превышает допустимый размер.")
            return json.loads(content)
        except HTTPError as error:
            if attempt or error.code not in (408, 429, 500, 502, 503, 504):
                raise
            LOG.warning("Запрос завершился HTTP %d. Повторяю.", error.code)
        except (TimeoutError, ConnectionError, HTTPException, URLError):
            if attempt:
                raise
            LOG.warning("Сбой соединения. Повторяю запрос.")
        sleep(1)


def fetch_vacancies(url, limit, timeout=60):
    if limit < 1:
        raise ValueError("Лимит должен быть положительным.")
    if timeout < 1:
        raise ValueError("Тайм-аут должен быть положительным.")
    source = urlsplit(url)
    if source.scheme not in ("http", "https") or not source.netloc:
        raise ValueError("Укажите HTTP(S)-адрес источника.")
    deadline = monotonic() + FETCH_SECONDS
    try:
        return fetch_pages(source, limit, timeout, deadline)
    except SourceUnavailable:
        if url != REGION_39:
            raise
        LOG.warning("HTTPS-адрес портала недоступен. Начинаю загрузку с официального HTTP-адреса.")
        return fetch_pages(urlsplit(REGION_39_HTTP), limit, timeout, deadline)


def fetch_pages(source, limit, timeout, deadline):
    filters = [(k, v) for k, v in parse_qsl(source.query, keep_blank_values=True)
               if k not in ("offset", "limit")]
    page_size = min(10, max(1, limit // 2))
    offset = 0
    collected = {}
    expected = None
    while len(collected) < limit:
        query = urlencode(filters + [("offset", offset), ("limit", page_size)])
        page_url = urlunsplit(source._replace(query=query, fragment=""))
        try:
            page = read_page(page_url, timeout, deadline)
            records = read_page_vacancies(page)
        except (ValueError, OSError, RecursionError, HTTPException) as error:
            message = f"Не удалось получить страницу offset={offset}: {error}"
            retry_gateway = isinstance(error, HTTPError) and error.code in (502, 503, 504)
            transport = isinstance(error, (URLError, TimeoutError, ConnectionError, HTTPException))
            tls = isinstance(getattr(error, "reason", error), ssl.SSLError)
            if retry_gateway or transport and not isinstance(error, HTTPError) and not tls:
                raise SourceUnavailable(message) from error
            raise ValueError(message) from error
        LOG.info("API offset=%d, limit=%d, записей=%d", offset, page_size, len(records))
        metadata = page.get("meta")
        total = metadata.get("total") if isinstance(metadata, dict) else None
        if str(total).isdecimal():
            expected = min(limit, int(total))
        previous_count = len(collected)
        for record in records:
            collected[record["id"].strip()] = record
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
        payload = fetch_vacancies(args.url, args.limit)
        write_report(args.output, payload)
    except (ValueError, OSError, RecursionError, HTTPException) as error:
        parser.exit(1, f"Ошибка загрузки: {error}\n")
    print(f"Сохранено вакансий: {payload['meta']['total']}. Файл: {args.output}")


if __name__ == "__main__":
    main()
