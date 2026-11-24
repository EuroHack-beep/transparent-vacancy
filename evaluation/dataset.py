import json
from pathlib import Path

from vacancy_facts import normalize
from validation import TOPICS


def normalized_text(value):
    return " ".join(normalize(value).split())


def load_dataset(path, split):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Нет размеченной выборки {split}: {path}. Используйте независимые группы вакансий.")
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            keys = ("text", "vacancy_id", "group", "source")
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k].strip()
                                                for k in keys):
                raise ValueError(f"{split}, строка {number}: заполните text, vacancy_id, group и source.")
            if row.get("topic") not in TOPICS or type(row.get("label")) is not int or row["label"] not in (0, 1):
                raise ValueError(f"{split}, строка {number}: ожидается тема и метка 0 или 1.")
            if split != "test" and row["source"] in {"organizers", "baseline-handwritten"}:
                raise ValueError(f"{split}: набор организаторов и фразы baseline исключены из настройки.")
            rows.append({**row, "text": normalized_text(row["text"])})
    if not rows:
        raise ValueError(f"Выборка {split} пуста.")
    return rows


def check_disjoint(left, right):
    for key in ("vacancy_id", "group", "text"):
        if {r[key] for r in left} & {r[key] for r in right}:
            raise ValueError(f"Выборки пересекаются по {key}.")


def topic_rows(rows, topic):
    selected = [row for row in rows if row["topic"] == topic]
    if {row["label"] for row in selected} != {0, 1}:
        raise ValueError(f"{topic}: для сравнения нужны примеры обеих меток.")
    return selected
