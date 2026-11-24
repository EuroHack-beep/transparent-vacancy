import argparse
import json
from http.client import HTTPException
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from analyze_vacancies import read_vacancies, write_report


REGION_39 = "https://opendata.trudvsem.ru/api/v1/vacancies/region/39"

MAX_PAGE_BYTES = 8 * 1024 * 1024
FETCH_SECONDS = 300


def read_page(url, deadline):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "transparent-vacancy/1.0"})
    for attempt in range(2):
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ValueError("Истекло время загрузки вакансий.")
            with urlopen(request, timeout=min(60, remaining)) as response:
                body = bytearray()
                while chunk := response.read1(65536):
                    if monotonic() >= deadline:
                        raise ValueError("Истекло время загрузки вакансий.")
                    if len(body) + len(chunk) > MAX_PAGE_BYTES:
                        raise ValueError("Страница API превышает допустимый размер.")
                    body.extend(chunk)
                return json.loads(body)
        except HTTPError as error:
            if attempt or error.code not in (408, 502, 503, 504):
                raise
        except (TimeoutError, ConnectionError, HTTPException, URLError):
            if attempt:
                raise
        sleep(1)


def fetch_vacancies(url, limit):
    if limit < 1:
        raise ValueError("Лимит должен быть положительным.")
    source = urlsplit(url)
    if source.scheme not in ("http", "https") or not source.netloc:
        raise ValueError("Укажите HTTP(S)-адрес источника.")
    filters = [(k, v) for k, v in parse_qsl(source.query, keep_blank_values=True)
               if k not in ("offset", "limit")]
    page_size = min(10, max(1, limit // 2))
    offset = 0
    collected = {}
    deadline = monotonic() + FETCH_SECONDS
    expected = None
    while len(collected) < limit:
        query = urlencode(filters + [("offset", offset), ("limit", page_size)])
        page_url = urlunsplit(source._replace(query=query, fragment=""))
        page = read_page(page_url, deadline)
        records = read_vacancies(page)
        previous_count = len(collected)
        for record in records:
            collected[record["id"]] = record
            if len(collected) == limit:
                break
        offset += 1
        metadata = page.get("meta")
        total = metadata.get("total") if isinstance(metadata, dict) else None
        if str(total).isdecimal():
            expected = min(limit, int(total))
        if records and len(collected) == previous_count:
            raise ValueError("На следующей странице нет новых вакансий.")
        if expected is not None and len(collected) >= expected:
            break
        if len(page["results"]["vacancies"]) < page_size:
            if expected is not None and len(collected) < expected:
                raise ValueError("API вернул меньше вакансий, чем указано в meta.total.")
            break

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
    try:
        payload = fetch_vacancies(args.url, args.limit)
        write_report(args.output, payload)
    except (ValueError, OSError, RecursionError, HTTPException) as error:
        parser.exit(1, f"Ошибка загрузки: {error}\n")
    print(f"Сохранено вакансий: {payload['meta']['total']}. Файл: {args.output}")


if __name__ == "__main__":
    main()
