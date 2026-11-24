"""Compare structured fields and prose, keeping source evidence for each issue."""

from dataclasses import dataclass
from html import unescape
from itertools import combinations
import re

from clarity_model import classify
from vacancy_facts import (
    ADDRESS, ALTERNATIVE, CITY, LOCALITY, PAY_CUE, REMOTE, ROTATION, SCHEDULE_CUE, UNPAID, WEEKDAYS, WORD_ROTATION,
    Pay, currency_code, employment_facts, location_facts, normalize, pay_conflict, pay_from_text,
    positive_salary, schedule_facts,
)


TOPICS = {
    "job_title": ("job-name", "название должности", "Укажите название должности и специализацию."),
    "duties": ("duty", "обязанности", "Какие задачи нужно выполнять регулярно и с чем предстоит работать?"),
    "requirements": ("requirement", "требования",
                     "Какие навыки, образование или опыт обязательны для этой работы?"),
    "salary": ("salary", "оплата", "Какой оклад или диапазон оплаты предусмотрен и за какой период?"),
    "schedule": ("schedule", "график", "В какие дни и часы предстоит работать или как чередуются смены?"),
    "location": ("addresses.address", "место работы",
                 "В каком населённом пункте и по какому адресу предстоит работать? Возможна ли удалённая работа?"),
    "employment": ("employment", "тип занятости",
                   "Уточните тип занятости, например полная, частичная или совместительство."),
}
STRUCTURED = {
    "job_title": ("job-name", "job_title", "jobTitle", "title"),
    "duties": ("duty", "duties", "responsibilities"),
    "requirements": ("requirements", "requirement", "qualification", "skills", "education", "experience"),
    "salary": ("salary",),
    "schedule": ("schedule", "shift", "workSchedule", "workingHours"),
    "location": ("addresses.address.location", "addresses.address.address", "addresses.address",
                 "workLocation", "work_address", "address", "location.address", "location.city",
                 "location", "city", "region.name"),
    "employment": ("employment", "employment_type", "employmentType"),
}
NARRATIVE = {
    "description", "conditions", "duty", "duties", "responsibilities", "requirements", "requirement",
    "qualification", "additionalInfo", "benefitDetails",
}
EMPTY = {
    "", "-", "—", "–", "не указано", "не указан", "не указаны", "нет данных", "не определено",
    "null", "n/a", "нет сведений", "отсутствует", "не заполнено", "уточняется",
}
HEADINGS = {
    "job_title": r"название должности|должность|вакансия",
    "duties": r"обязанности|задачи",
    "requirements": r"требования(?: к (?:кандидату|соискателю))?|квалификация",
    "salary": r"заработная плата|зарплата|оплата(?: труда)?|оклад",
    "schedule": r"график(?: работы)?|режим работы",
    "location": r"адрес(?: работы)?|место работы|формат работы",
    "employment": r"тип занятости|занятость",
    "conditions": r"условия(?: работы)?",
}
HEADING = re.compile(
    r"(?:^|(?<=[\n.;]))[ \t]*(?:[-–•][ \t]*)?(?:"
    + "|".join(fr"(?P<{topic}>{regex})" for topic, regex in HEADINGS.items())
    + r")[ \t]*(?::|—|\n)[ \t]*", re.I,
)
DUTY_CUE = re.compile(
    r"\b(?:предстоит|выполнять|вести|обслуживать|оформлять|консультировать|проверять|"
    r"принимать|каталогизировать|составлять|регистрировать|разрабатывать|подготавливать|"
    r"обучать|ремонтировать|убирать|уборка|выдача|выполнение поручений)\b"
)
DUTY_GENERIC = re.compile(
    r"(?:все|иные|прочие|другие|различные|поставленные)\s+(?:задачи|обязанности)|"
    r"(?:выполнение|выполнять)\s+(?:текущих\s+)?поручени\w*|"
    r"(?:по|согласно)\s+(?:должностн\w*\s+инструкци\w*|указани\w*\s+руководства|"
    r"(?:внутренн\w*\s+)?регламент\w*)|"
    r"поручени\w*\s+(?:руководителя|начальника)"
)
DUTY_DETAIL = re.compile(
    r"\b(?:обслужив\w*|настраива\w*|настройк\w*|ремонт\w*|консультир\w*|"
    r"каталогизир\w*|регистрир\w*|оформля\w*|архивир\w*|уборка)\s+(?:[а-яa-z0-9]{3,})"
)
REQUIREMENT_CUE = re.compile(
    r"\b(?:опыт\w*|образовани\w*|диплом\w*|знани\w*|владени\w*|умени\w*|"
    r"удостоверени\w*|навык\w*|сертификат\w*|разряд\w*|категори\w*|стаж(?:ем|а|у|е)?)\b"
)
REQUIREMENT_GENERIC = re.compile(
    r"\b(?:опыт приветствуется|желателен опыт|релевантный опыт|"
    r"уверенный пользователь (?:пк|компьютера)|профильное образование)\b|"
    r"^(?:(?:требуется|нужен|необходим) опыт(?: работы)?|опыт(?: работы)? обязателен)[.!]?$"
)
REQUIREMENT_SUBSTANCE = re.compile(
    r"\b(?:без опыта|не требуется|не предъявляются|"
    r"высш\w*|средн\w*|бакалавр\w*|магистр\w*|профессиональн\w* образовани\w*|"
    r"excel|linux|sql|git|word|1с|1c|markdown|python|категори\w*\s+[а-яa-z]\b|"
    r"\d+\s*(?:лет|год\w*|месяц\w*|разряд\w*))\b"
)
SOFT_SKILL = re.compile(
    r"\b(?:ответственност\w*|ответственн\w*|пунктуальност\w*|внимательност\w*|"
    r"внимательн\w*|коммуникабельн\w*|исполнительн\w*|стрессоустойчив\w*|"
    r"обучаемост\w*|дисциплинированн\w*|аккуратност\w*|добросовестн\w*)\b"
)
GENERIC_TITLE = {"специалист", "менеджер", "работник", "сотрудник", "помощник", "стажер", "оператор"}
PAY_DUTY = re.compile(
    r"(?:начисля\w*|рассчитыва\w*|учет|выплачи\w*)\s+(?:\w+\s+){0,2}"
    r"(?:зарплат\w*|заработн\w*\s+плат\w*)"
)
SHIFT_DUTY = re.compile(r"(?:вести|составлять|подготавливать|контролировать)\s+график\w*")
CLOCK_CUE = re.compile(r"\b\d{1,2}:[0-5]\d\b|\bс\s+\d{1,2}(?:\.[0-5]\d)?\s+(?:до|по)\s+\d")
RECRUITMENT = re.compile(r"собеседовани\w*|встреч\w* с рекрутером|дни приема кандидатов|прием резюме")
JOB_SCHEDULE = re.compile(r"рабоч\w*\s+(?:день|дни|недел\w*|час\w*)|график\w*\s+работ\w*|"
                          r"(?<![а-я])смен(?:ы|а|у|е)\b|выходные")
JOB_LOCATION = re.compile(r"место работы|рабочее место|рабочая площадка|работа в(?:\s|$)|работать в(?:\s|$)")
OPEN_OPTIONS = re.compile(r"по договоренности|друг\w* вариант\w*|и так далее")
PLACE_GENERIC = re.compile(
    r"на территории работодателя|на объектах компании|по месту нахождения|адрес обсуждается|центр города"
)
EMPLOYMENT_CUE = re.compile(r"занятост\w*|совместительств\w*|договор\w*|ставк\w*|стажировк\w*")
LOCATION_CUE = re.compile(r"место работы|адрес|территори\w* работодателя|удален\w*|дистанционн\w*|гибрид\w*")


@dataclass(frozen=True)
class Evidence:
    field: str
    value: str | int | float
    structured: bool = False
    labeled: bool = False

    @property
    def text(self):
        return normalize(clean(self.value))


def clean(value):
    return " ".join(plain(value).split())


def plain(value):
    text = re.sub(r"</?(?:p|div|li|ul|ol|br|tr)\b[^>]*>", "\n", str(value), flags=re.I)
    return unescape(re.sub(r"<[^>]*>", " ", text)).strip()


def leaves(value, path=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, f"{path}[{index}]")
    elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
        if normalize(clean(value)).strip(" .") not in EMPTY:
            yield path, value


def split_prose(value):
    text = plain(value)
    headings = list(HEADING.finditer(text))
    sections = [(None, text[:headings[0].start()] if headings else text)]
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        sections.append((heading.lastgroup, text[heading.end():end]))
    for label, section in sections:
        # The negative lookbehinds keep common address and currency abbreviations intact.
        lines = re.split(
            r"\n+|(?<!(?i:\bул\.))(?<!(?i:\bд\.))(?<!(?i:\bг\.))(?<!(?i:\bп\.))"
            r"(?<!(?i:\bпос\.))(?<!(?i:\bруб\.))(?<!(?i:\bтыс\.))(?<=\.)\s+(?=[А-ЯЁA-Z])", section,
        )
        for line in lines:
            text = normalize(clean(line))
            choice = bool(ALTERNATIVE.search(text) and (label == "schedule" or SCHEDULE_CUE.search(text)))
            for part in [line] if choice else re.split(r";\s*", line):
                if normalize(clean(part)).strip(" .;—–") not in EMPTY:
                    yield label, part.strip()


def prose_topics(text, label):
    if not PAY_DUTY.search(text) and (
        PAY_CUE.search(text) or UNPAID.search(text) or re.search(r"\d[\d .,]*\s*(?:₽|рубл)", text)
    ):
        yield "salary"
    recruitment = bool(RECRUITMENT.search(text))
    work_schedule = SCHEDULE_CUE.search(text)
    if not work_schedule and label != "location" and not ADDRESS.search(text):
        work_schedule = any(regex.search(text) for regex in (ROTATION, WEEKDAYS, WORD_ROTATION, CLOCK_CUE))
    if work_schedule and not SHIFT_DUTY.search(text) and (not recruitment or JOB_SCHEDULE.search(text)):
        yield "schedule"
    job_location = JOB_LOCATION.search(text)
    if (not recruitment or job_location) and (
        LOCATION_CUE.search(text) or ADDRESS.search(text) or REMOTE.search(text) or job_location and CITY.search(text)
    ):
        yield "location"
    if (EMPLOYMENT_CUE.search(text) and not PAY_CUE.search(text)) or any(employment_facts(text).values()):
        yield "employment"
    if re.match(r"(?:требуется|ищем|приглашаем)\s+(?!(?:опыт|знание|образование)\b)[а-я]{3,}", text):
        yield "job_title"
    if REQUIREMENT_CUE.search(text):
        yield "requirements"
    if DUTY_CUE.search(text) and not re.match(r"(?:умение|опыт|навык)\w*\b", text):
        yield "duties"


def evidence_by_topic(vacancy):
    relevant = NARRATIVE | {path.split(".")[0] for paths in STRUCTURED.values() for path in paths}
    evidence = {topic: [] for topic in TOPICS}
    for path, value in leaves({key: val for key, val in vacancy.items() if key in relevant}):
        lookup = re.sub(r"\[\d+\]", "", path)
        if lookup.rsplit(".", 1)[-1] in {"id", "code", "guid", "lat", "lng", "longitude", "latitude"}:
            continue
        direct = [topic for topic, paths in STRUCTURED.items() if lookup in paths or any(
            lookup.startswith(field + ".") for field in paths if field != "region.name"
        )]
        if lookup in {"duty", "duties", "responsibilities"}:
            direct = [topic for topic in direct if topic != "duties"]
        for topic in direct:
            evidence[topic].append(Evidence(path, value, structured=True))
        if lookup in {"schedule", "shift", "workSchedule"} and REMOTE.search(normalize(clean(value))):
            evidence["location"].append(Evidence(path, value, structured=True))
        if lookup.split(".")[0] not in NARRATIVE or not isinstance(value, str):
            continue
        for label, clause in split_prose(value):
            inferred = set(prose_topics(normalize(clean(clause)), label))
            if label and label != "conditions":
                # Headings establish the topic. Detect other explicit conditions on the same line.
                inferred.add(label)
            if lookup in {"duty", "duties", "responsibilities"} and (not inferred or "duties" in inferred):
                inferred.add("duties")
            for topic in TOPICS:
                if topic not in inferred or (topic in direct and label != topic):
                    continue
                evidence[topic].append(Evidence(
                    path, clause, structured=lookup in STRUCTURED[topic], labeled=label == topic,
                ))
    return {topic: list(dict.fromkeys(items)) for topic, items in evidence.items()}


VAGUE = {
    "job_title": "По названию трудно понять роль и специализацию сотрудника.",
    "duties": "Формулировка не объясняет, какие задачи сотрудник будет выполнять регулярно.",
    "requirements": "Формулировка не даёт конкретных обязательных требований.",
    "salary": "Формулировка не указывает сумму или диапазон оплаты.",
    "schedule": "Формулировка оставляет открытым вопрос о рабочих днях, часах или порядке смен.",
    "location": "По указанным сведениям неясно, где предстоит работать.",
    "employment": "По формулировке невозможно определить тип занятости.",
}
CONFLICTS = {
    "job_title": ("Указаны разные названия должности.", "Уточните единую должность для этой карточки."),
    "duties": ("Одна из обязанностей указана и одновременно исключена.",
               "Уточните, входит ли эта задача в обязанности сотрудника."),
    "requirements": ("Есть разные сведения об обязательности опыта.",
                     "Уточните, нужен ли опыт, и укажите минимальный стаж, если он обязателен."),
    "salary": ("Указаны несовместимые суммы оплаты.",
               "Согласуйте размер оплаты между полями и уточните период и учёт НДФЛ."),
    "schedule": ("Указаны несовместимые рабочие часы или варианты чередования смен.",
                 "Уточните рабочие часы и схему смен. Если есть варианты на выбор, явно перечислите их."),
    "location": ("Указаны разные населённые пункты или форматы присутствия.",
                 "Уточните фактическое место работы и согласуйте адрес и формат присутствия."),
    "employment": ("Указаны несовместимые условия занятости.",
                   "Уточните фактический тип занятости или опишите, когда доступен каждый вариант."),
}


def labeled_by_model(topic, item):
    variants = [item.text]
    if topic in {"salary", "schedule"} and (item.structured or item.labeled):
        label = {"salary": "зарплата", "schedule": "график"}[topic]
        variants.append(f"{label} {item.text}")
    return any(classify(variant, topic=topic) == topic for variant in variants)


def is_specific(topic, item, vague):
    text = item.text
    if topic == "job_title":
        title = re.sub(r"^(?:требуется|ищем|приглашаем)\s+", "", text).strip(". ")
        return not vague and title not in GENERIC_TITLE and bool(re.search(r"\w{3,}", title))
    if topic == "duties":
        return (bool(DUTY_DETAIL.search(text))
                or not (vague or DUTY_GENERIC.search(text)) and len(text.split()) >= 2)
    if topic == "requirements":
        if re.search(r"(?:^|\.)experience$", item.field) and re.fullmatch(r"\d+(?:[.,]\d+)?", text):
            return True
        if REQUIREMENT_SUBSTANCE.search(text):
            return True
        if vague or REQUIREMENT_GENERIC.search(text):
            return False
        return bool(REQUIREMENT_CUE.search(text) and len(text.split()) >= 3
                    or item.structured and not SOFT_SKILL.search(text) and len(text.split()) >= 2)
    if topic == "salary":
        return pay_from_text(text, structured=item.structured or item.labeled) is not None
    if topic == "schedule":
        if RECRUITMENT.search(text) and not JOB_SCHEDULE.search(text):
            return False
        schedule = schedule_facts(text)
        return bool(schedule["rotation"] or schedule["times"] or re.search(
            r"\b(?:выходные\s+в\s+субботу|по субботам|по воскресеньям)\b", text
        ))
    if topic == "location":
        if RECRUITMENT.search(text) and not JOB_LOCATION.search(text):
            return False
        facts = location_facts(text)
        return bool(ADDRESS.search(text) or LOCALITY.search(text) or facts["city"] or "remote" in facts["format"]
                    or item.structured and (item.field == "city" or item.field == "location.city")
                    and not PLACE_GENERIC.search(text))
    return any(employment_facts(text, structured=item.structured or item.labeled).values()) or bool(
        re.search(r"\b(?:стажировк\w*|подработк\w*|разов\w*\s+работ\w*)\b", text)
    )


def conflict_result(topic, evidence, problem=None, recommendation=None):
    default_problem, default_recommendation = CONFLICTS[topic]
    fields = list(dict.fromkeys(item.field for item in evidence))
    if len(fields) == len(evidence):
        source_value = {item.field: item.value for item in evidence}
    else:
        source_value = [{"field": item.field, "value": item.value} for item in evidence]
    return {"topic": topic, "source_fields": fields, "source_value": source_value,
            "problem": problem or default_problem,
            "recommendation": recommendation or default_recommendation, "method": "rule"}


def salary_conflict(vacancy, items):
    low = positive_salary(vacancy.get("salary_min"))
    high = positive_salary(vacancy.get("salary_max"))
    bounds = [Evidence(field, vacancy[field]) for field, amount in
              (("salary_min", low), ("salary_max", high)) if amount is not None]
    if low is not None and high is not None and low > high:
        return conflict_result(
            "salary", bounds, "Минимальная зарплата указана выше максимальной.",
            "Уточните нижнюю и верхнюю границы зарплаты и исправьте диапазон.",
        )
    offers = [(pay, [item]) for item in items if not ALTERNATIVE.search(item.text)
              if (pay := pay_from_text(item.text, item.structured or item.labeled)) is not None]
    if bounds:
        currency = currency_code(normalize(str(vacancy.get("currency") or "")).strip())
        offers.insert(0, (Pay(low, high, currency=currency), bounds))
    for pay, source in offers:
        if pay.low is not None and pay.high is not None and pay.low > pay.high:
            return conflict_result("salary", source, "Нижняя граница оплаты выше верхней.")
    for (first, first_source), (second, second_source) in combinations(offers, 2):
        if pay_conflict(first, second):
            return conflict_result("salary", first_source + second_source)
    return None


def axis_values(topic, item):
    text = item.text
    if topic == "schedule":
        if RECRUITMENT.search(text) and not JOB_SCHEDULE.search(text):
            return {}
        facts = schedule_facts(text)
        if ALTERNATIVE.search(text):
            return {"rotation": facts["rotation"]} if len(facts["rotation"]) > 1 and not OPEN_OPTIONS.search(text) else {}
        return facts
    if ALTERNATIVE.search(text):
        return {}
    if topic == "location":
        if RECRUITMENT.search(text) and not JOB_LOCATION.search(text):
            return {}
        return location_facts(text)
    if topic == "employment":
        return employment_facts(text, structured=item.structured or item.labeled)
    if topic == "requirements":
        if re.search(r"(?:^|\.)experience$", item.field) and re.fullmatch(r"\d+(?:[.,]\d+)?", text):
            return {"experience": {"required" if positive_salary(text) is not None else "none"}}
        if re.search(r"\bбез опыта|опыт\w*\s+(?:не требуется|не нужен|не обязателен)", text):
            return {"experience": {"none"}}
        if not re.search(r"желател\w*|приветствуется|предпочтительн\w*", text) and re.search(
            r"\bопыт\b.{0,70}\b(?:обязател\w*|от\s+(?:[1-9]\d*|года|одного|двух|трех)|не менее)\b", text
        ):
            return {"experience": {"required"}}
    if topic == "duties":
        if re.search(r"\b(?:без холодных звонков|холодные звонки исключены)\b", text):
            return {"calls": {"none"}}
        if re.search(r"\b(?:совершать|выполнять|осуществлять)\s+холодные звонки\b", text):
            return {"calls": {"required"}}
    return {}


def title_conflict(items):
    titles = [item for item in items if (item.structured or item.labeled)
              and is_specific("job_title", item, False) and not ALTERNATIVE.search(item.text)]
    for first, second in combinations(titles, 2):
        first_words, second_words = (set(re.findall(r"\w+", item.text)) for item in (first, second))
        if first_words and second_words and not (first_words <= second_words or second_words <= first_words):
            return conflict_result("job_title", [first, second])
    return None


def cross_field_conflict(topic, items):
    pairs = [(item, axis_values(topic, item)) for item in items]
    address_options = {
        axis: set().union(*(axes.get(axis, set()) for item, axes in pairs
                            if item.field.startswith("addresses.address")))
        for axis in ("city", "address")
    } if topic == "location" else {}
    for (first, first_axes), (second, second_axes) in combinations(pairs, 2):
        for axis, left in first_axes.items():
            right = second_axes.get(axis, set())
            if not left or not right or left & right:
                continue
            if (len(left) != 1 and not is_schedule_choice(topic, axis, first)
                    or len(right) != 1 and not is_schedule_choice(topic, axis, second)):
                continue
            options = address_options.get(axis, set())
            if len(options) > 1:
                first_address = first.field.startswith("addresses.address")
                second_address = second.field.startswith("addresses.address")
                other = right if first_address else left
                if first_address and second_address or (first_address or second_address) and other <= options:
                    continue
            return conflict_result(topic, [first, second])
    return None


def is_schedule_choice(topic, axis, item):
    return topic == "schedule" and axis == "rotation" and bool(ALTERNATIVE.search(item.text))


def check_conditions(vacancy):
    issues = []
    for topic, items in evidence_by_topic(vacancy).items():
        if topic == "salary":
            contradiction = salary_conflict(vacancy, items)
        elif topic == "job_title":
            contradiction = title_conflict(items)
        else:
            contradiction = cross_field_conflict(topic, items)
        if contradiction:
            issues.append(contradiction)
            continue
        if topic == "salary" and any(positive_salary(vacancy.get(field)) is not None
                                     for field in ("salary_min", "salary_max")):
            continue
        if topic == "salary":
            items = [item for item in items if not re.fullmatch(r"0(?:[.,]0+)?\s*(?:руб\.?|₽)?", item.text)]

        labels = [(item, labeled_by_model(topic, item)) for item in items]
        if any(is_specific(topic, item, ambiguous) for item, ambiguous in labels):
            continue
        primary, name, question = TOPICS[topic]
        if topic == "schedule" and any(axis_values(topic, item).get("hours") for item in items):
            question = "В какие дни предстоит работать и как чередуются смены?"
        model_evidence = next((item for item, ambiguous in labels if ambiguous), None)
        if item := model_evidence or next(iter(items), None):
            problem, fields, value = VAGUE[topic], [item.field], item.value
        else:
            looked_up = (primary,) + STRUCTURED[topic] + tuple(sorted(NARRATIVE))
            if topic == "salary":
                looked_up += ("salary_min", "salary_max")
            fields = list(dict.fromkeys(looked_up))
            problem, value = f"В карточке не указано условие по теме «{name}».", ""
        issues.append({"topic": topic, "source_fields": fields, "source_value": value,
                       "problem": problem, "recommendation": question,
                       "method": "nearest_neighbor" if model_evidence else "rule"})
    return issues
