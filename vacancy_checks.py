"""Cross-field checks and evidence for the vacancy report."""
import re
from html import unescape

from clarity_model import classify


TOPICS = {
    "job_title": ("job-name", "название должности", "Как называется должность?"),
    "duties": ("duty", "обязанности", "Какие задачи сотрудник будет выполнять регулярно?"),
    "requirements": ("requirements", "требования", "Какие навыки, образование или опыт нужны для этой работы?"),
    "salary": ("salary", "оплата", "Какой оклад или диапазон оплаты предусмотрен и за какой период?"),
    "schedule": ("schedule", "график", "В какие дни и часы предстоит работать?"),
    "location": ("addresses.address", "место работы", "По какому адресу предстоит работать или возможна ли удалённая работа?"),
    "employment": ("employment", "тип занятости", "Какой тип занятости предусмотрен для этой должности?"),
}

# Only read fields about the job. A company's legal address or telephone
# number cannot establish where the work is done or how much it pays.
STRUCTURED = {
    "job_title": ("job-name",),
    "duties": ("duty", "responsibilities"),
    "requirements": ("requirements", "requirement", "qualification", "skills"),
    "salary": ("salary",),
    "schedule": ("schedule", "shift"),
    "location": ("addresses.address.location", "workLocation"),
    "employment": ("employment",),
}
NARRATIVE = ("description", "conditions", "duty", "requirements", "requirement", "qualification", "additionalInfo", "benefitDetails")
PATTERNS = {
    "job_title": r"(?:должность|вакансия)\s*[:—]\s*\w+|(?:требуется|ищем)\s+(?!(?:опыт|образование|знание|сотрудник)\b)[а-яё]{3,}",
    "duties": r"обязанности\s*[:—]\s*\w+|предстоит\s+\w+|выполнять|вести уч[её]т|обслуживать|оформлять|консультировать|проверять|принимать (?:заказы|документы)",
    "requirements": r"опыт[а]?\s+работы|без опыта|образовани[ея]|знани[ея]|умение|требования\s*[:—]\s*\w+",
    "salary": r"\d[\d\s.,–—-]*(?:тыс|руб|₽)|(?:оклад|зарплата|оплата|ставка|заработная плата)\s*[:—-]?\s*(?:от\s+)?\d",
    "schedule": r"\b[1-6]\s*[/хx×]\s*[1-6]\b|\b(?:[01]?\d|2[0-3]):[0-5]\d\b|\bс\s+\d{1,2}(?:\.\d{2})?\s+до\s+\d{1,2}\b|понедельник|по будням|пятиднев|два через два|смен[аы]\s+(?:по\s+)?\d+\s+час",
    "location": r"удал[её]нн|дистанционн|(?:адрес|место работы)\s*[:—]\s*\w+|\b(?:улица|проспект|ул\.|пр-т)\s+\w+",
    "employment": r"(?:полная|частичная|временная|сезонная|постоянная) занятость|совместительств|на полную ставку|на полставки|неполная занятость",
}
VAGUE = {
    "schedule": "Формулировка оставляет открытым вопрос о рабочих днях и часах.",
    "salary": "Формулировка не указывает сумму оплаты.",
}
EMPTY = {"", "-", "—", "не указано", "не указан", "не указаны", "нет данных", "не определено"}


def clean(value):
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", str(value))).split())


def leaves(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, f"{path}[{index}]")
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        if clean(value).casefold() not in EMPTY:
            yield path, value


def ambiguous_evidence(topic, fields):
    for path, value in fields:
        phrases = re.split(r"[.;!?\n]+", clean(value))
        if path == "salary" or path == "schedule" or path.startswith("shift["):
            label = "зарплата" if path == "salary" else "график"
            phrases.extend(f"{label} {phrase}" for phrase in tuple(phrases) if phrase.strip())
        if any(classify(phrase) == topic for phrase in phrases):
            return {"topic": topic, "source_fields": [path], "source_value": value,
                    "problem": VAGUE[topic], "recommendation": TOPICS[topic][2],
                    "method": "nearest_neighbor"}
    return None


def check_conditions(vacancy, has_salary_bound):
    relevant = set(NARRATIVE)
    for paths in STRUCTURED.values():
        relevant.update(path.split(".")[0] for path in paths)
    fields = list(leaves({k: v for k, v in vacancy.items() if k in relevant}))
    # Keep original paths for the report and normalise array indices for lookup.
    values = [(path, re.sub(r"\[\d+\]", "", path), value) for path, value in fields]
    prose = [(path, value) for path, key, value in values if key.split(".")[0] in NARRATIVE]
    narrative = "\n".join(clean(value) for _, value in prose)
    issues = []
    for topic, (primary, name, question) in TOPICS.items():
        direct = [(path, value) for path, key, value in values
                  if any(key == p or key.startswith(p + ".") for p in STRUCTURED[topic])]
        if topic == "salary" and has_salary_bound:
            continue
        if topic == "salary":
            direct = [(p, v) for p, v in direct if clean(v) not in ("0", "0.0")]
        context = narrative + "\n" + "\n".join(clean(value) for _, value in direct)
        if re.search(PATTERNS[topic], context, re.I):
            continue
        if topic in VAGUE:
            if topic == "salary" and any(re.search(r"\b[1-9]\d{2,}\b", clean(v)) for _, v in direct):
                continue
            issue = ambiguous_evidence(topic, direct + prose)
            if issue:
                issues.append(issue)
                continue
            if topic == "schedule" and re.search(r"(?:сменный график|полный рабочий день|вахтовый метод)", narrative, re.I):
                continue
        if not direct:
            issues.append({"topic": topic, "source_fields": [primary], "source_value": "",
                           "problem": f"В карточке не удалось найти сведения по теме «{name}».",
                           "recommendation": question, "method": "rule"})
    return issues
