import json
import math
import os
import re


LOCAL_BYTES = 64 * 1024 * 1024
DEFAULT_DEPTH = 32
DEFAULT_FIELD_LENGTH = 32768
MAX_ITEMS = 600_000
MAX_REPORT_ITEMS = 3_000_000
SYNTAX = re.compile(r'["\\\[\]{},:]')
ISSUE_KEYS = {"topic", "source_fields", "source_value", "problem", "recommendation", "method"}
TOPICS = {"job_title", "duties", "requirements", "salary", "schedule", "location", "employment"}


def positive_setting(name, default, maximum):
    try:
        value = int(os.environ.get(name) or default)
    except ValueError:
        raise ValueError(f"{name}: ожидается целое число.") from None
    if not 1 <= value <= maximum:
        raise ValueError(f"{name}: допустимы значения от 1 до {maximum}.")
    return value


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("В JSON найден повторяющийся ключ.")
        result[key] = value
    return result


def invalid_constant(_):
    raise ValueError("JSON содержит нечисловое или бесконечное значение.")


def finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        invalid_constant(value)
    return parsed


def bounded_int(value):
    if len(value) > 128:
        raise ValueError("Число в JSON слишком длинное.")
    return int(value)


def preflight(text, max_depth, max_field, max_items):
    depth = items = 0
    string_start = None
    escaped_at = -1
    for token in SYNTAX.finditer(text):
        char = token.group()
        pos = token.start()
        if string_start is not None:
            if pos - string_start > max_field * 6:
                raise ValueError("Превышена длина поля JSON.")
            if pos == escaped_at:
                continue
            if char == "\\":
                escaped_at = pos + 1
            elif char == '"':
                string_start = None
            continue
        items += 1
        if items > max_items:
            raise ValueError("Превышен бюджет элементов JSON.")
        if char == '"':
            string_start = pos + 1
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise ValueError("Превышена глубина JSON.")
        elif char in "]}":
            depth -= 1
    if string_start is not None and len(text) - string_start > max_field * 6:
        raise ValueError("Превышена длина поля JSON.")


def load_json(data, *, max_items=MAX_ITEMS):
    max_depth = positive_setting("VACANCY_JSON_DEPTH", DEFAULT_DEPTH, 64)
    max_field = positive_setting("VACANCY_FIELD_CHARS", DEFAULT_FIELD_LENGTH, 65536)
    text = data.decode("utf-8-sig")
    preflight(text, max_depth, max_field, max_items)
    document = json.loads(text, object_pairs_hook=unique_object, parse_constant=invalid_constant,
                          parse_float=finite_float, parse_int=bounded_int)
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if len(value) > max_field:
                raise ValueError("Превышена длина поля JSON.")
            if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                raise ValueError("JSON содержит непарный суррогат Unicode.")
        elif isinstance(value, dict):
            pending.extend(value)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return document


def load_local(path, *, max_items=MAX_ITEMS):
    limit = positive_setting("VACANCY_LOCAL_BYTES", LOCAL_BYTES, LOCAL_BYTES)
    with path.open("rb") as file:
        data = file.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Локальный JSON превышает допустимый размер.")
    return load_json(data, max_items=max_items)


def validate_report(rows, vacancy_ids=None):
    if not isinstance(rows, list):
        raise ValueError("Отчёт должен быть массивом объектов.")
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Каждая запись отчёта должна быть объектом.")
        identifier = row.get("id")
        if not isinstance(identifier, str) or identifier.strip() in ids or not identifier.strip():
            raise ValueError("Отчёт содержит некорректный или повторяющийся ID.")
        ids.add(identifier.strip())
        if "url" in row and not isinstance(row["url"], str):
            raise ValueError("Отчёт содержит некорректную ссылку.")
        issues = row.get("issues")
        if not isinstance(issues, list):
            raise ValueError("Отчёт должен содержать массив issues.")
        for issue in issues:
            if not isinstance(issue, dict) or not ISSUE_KEYS <= issue.keys() or (
                not isinstance(issue["topic"], str) or issue["topic"] not in TOPICS
            ):
                raise ValueError("Отчёт содержит некорректное замечание.")
            if not isinstance(issue["source_fields"], list) or not all(
                isinstance(field, str) for field in issue["source_fields"]
            ) or not all(isinstance(issue[key], str) and issue[key] for key in
                         ("problem", "recommendation", "method")):
                raise ValueError("Отчёт содержит некорректные поля замечания.")
    if vacancy_ids is not None and ids != vacancy_ids:
        raise ValueError("Отчёт не покрывает все уникальные входные вакансии.")
