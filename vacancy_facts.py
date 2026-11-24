"""Extract comparable working conditions from Russian vacancy text."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


def normalize(text):
    return text.casefold().replace("ё", "е")


ALTERNATIVE = re.compile(r"\b(?:или|на выбор|по выбору|один из вариантов)\b")
CLOCK_RANGE = re.compile(
    r"\b(?:с\s*)?([01]?\d|2[0-3])(?:[:.]([0-5]\d))?\s*"
    r"(?:до|по|[-–—])\s*([01]?\d|2[0-3])(?:[:.]([0-5]\d))?\b"
    r"(?!\s*(?:раз|дней|дня|смен(?:ы)?|недел[ьи]|месяц\w*|тыс\w*|руб\w*)\b|\s*₽)"
)
ROTATION = re.compile(r"(?<![\d/])(?<!\d\.)([1-9]\d?)\s*[/xх×]\s*([1-9]\d?)(?![\d/]|\.\d)")
WEEKDAYS = re.compile(
    r"пятиднев\w*|по будням|с понедельника по пятницу|(?:понедельник|пн)\s*[-–—]\s*(?:пятница|пт)"
)
SCHEDULE_CUE = re.compile(r"график|\bсмен\w*|режим работы|рабочий день|рабочая неделя|выходные|вахт")
NUMBER_WORDS = {"один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "семь": 7, "сутки": 1}
WORD_ROTATION = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\s+через\s+(" +
                           "|".join(NUMBER_WORDS) + r")\b")


def schedule_facts(text):
    rotations = {(int(a), int(b)) for a, b in ROTATION.findall(text)}
    rotations.update((NUMBER_WORDS[a], NUMBER_WORDS[b]) for a, b in WORD_ROTATION.findall(text))
    if WEEKDAYS.search(text):
        rotations.add((5, 2))
    # Bare numeric ranges are timestamps only in a schedule field or clause.
    times = {(int(h1), int(m1 or 0), int(h2), int(m2 or 0))
             for h1, m1, h2, m2 in CLOCK_RANGE.findall(text)}
    hours = {Decimal(amount.replace(",", ".")) for amount in re.findall(
        r"\b(\d{1,2}(?:[,.]\d+)?)\s*час\w*\b", text) if SCHEDULE_CUE.search(text)}
    return {"rotation": rotations, "times": times, "hours": hours}


CITY = re.compile(
    r"\b(Калининград|Советск|Черняховск|Гусев|Балтийск|Светлогорск|Зеленоградск|"
    r"Гурьевск|Гвардейск|Пионерск|Правдинск|Полесск|Славск|Неман|Краснознаменск|"
    r"Нестеров|Багратионовск|Мамоново|Ладушкин|Озерск|Москва|Санкт-Петербург|"
    r"Светлый|Светлом|Янтарный|Янтарном)(?:е|а|у|ом|ий)?\b", re.I
)
ADDRESS = re.compile(r"(?:\bул(?:ица|\.)|\bпроспект|\bпр[.-]т|\bшоссе|\bпереулок|"
                     r"\bнабережная|\bпроезд|\bбульвар|\bплощадь|\bпер\.)\s*[^,;]+")
STREET_HOUSE = re.compile(
    r"\b(ул(?:ица|\.)|проспект|пр[.-]т|шоссе|переулок|пер\.|набережная|проезд|бульвар|площадь)"
    r"\s*([^,;\d]+?)[ ,]+(?:(?:д\.|дом)\s*)?(\d+[а-я]?(?:/\d+)?)(?![\dа-я/])"
)
LOCALITY = re.compile(r"\b(?:г\.|город|пос\.|поселок|село|деревня)\s+[а-яё-]{2,}")
REMOTE = re.compile(r"удален\w*|дистанционн\w*|"
                    r"\bработ\w*\s+(?:полностью\s+)?(?:из дома|на дому)\b|^(?:из дома|на дому)[.!]?$")
REMOTE_NEGATED = re.compile(r"\b(?:без|не|нет|исключена|невозможна)\s+(?:удален\w*|дистанционн\w*)|"
                            r"\bне\s+работ\w*\s+(?:из дома|на дому)\b|"
                            r"\b(?:удален\w*|дистанционн\w*)(?:\s+(?:работ\w*|формат))?\s+"
                            r"(?:невозмож\w*|исключ\w*|не\s+(?:предусмотр\w*|допуска\w*|возмож\w*))")
OFFICE = re.compile(r"\b(?:в офисе|очная работа|очно|на месте работодателя|работа на объекте)\b")
MIXED = re.compile(r"гибрид\w*|возможн\w*|частично удален\w*")


def location_facts(text):
    cities = {normalize(city) for city in CITY.findall(text)}
    if cities & {"светлом", "янтарном"}:
        cities = {{"светлом": "светлый", "янтарном": "янтарный"}.get(city, city) for city in cities}
    formats = set()
    if REMOTE_NEGATED.search(text):
        formats.add("office")
    elif not (MIXED.search(text) or ALTERNATIVE.search(text)):
        if REMOTE.search(text) and not OFFICE.search(text):
            formats.add("remote")
        elif OFFICE.search(text) and not REMOTE.search(text):
            formats.add("office")
    streets = {"ул.": "улица", "пр-т": "проспект", "пр.т": "проспект", "пер.": "переулок"}
    addresses = {(streets.get(kind, kind), " ".join(name.strip().split()), house)
                 for kind, name, house in STREET_HOUSE.findall(text)}
    return {"city": cities, "address": addresses, "format": formats}


EMPLOYMENT = {
    "load": {
        "full": r"(?<!не )\b(?:полн\w*\s+занятост\w*|на полную ставку|полная ставка|1[,.]0 ставки)\b",
        "part": r"\b(?:частичн\w*\s+занятост\w*|не\s*полн\w*\s+занятост\w*|"
                r"полставки|(?:0[,.][1-9]\d*)\s*ставки)\b",
    },
    "term": {
        "permanent": r"\b(?:постоянн\w*\s+(?:работ\w*|занятост\w*)|бессрочн\w*\s+договор\w*)\b",
        "temporary": r"\b(?:(?:временн\w*|сезонн\w*)\s+(?:работ\w*|занятост\w*)|"
                     r"срочн\w*\s+(?:трудов\w*\s+)?договор\w*)\b",
    },
    "primary": {
        "main": r"\bосновн\w*\s+мест\w*\s+работ\w*\b",
        "secondary": r"\bсовместительств\w*\b",
    },
    "contract": {
        "labour": r"\bтрудов\w*\s+договор\w*\b",
        "civil": r"\b(?:гпх|гражданско-правов\w*\s+договор\w*)\b",
    },
}


def employment_facts(text, structured=False):
    result = {axis: {value for value, regex in items.items() if re.search(regex, text)}
              for axis, items in EMPLOYMENT.items()}
    if structured:
        by_label = {
            "полная": ("load", "full"), "частичная": ("load", "part"),
            "временная": ("term", "temporary"), "постоянная": ("term", "permanent"),
            "сезонная": ("term", "temporary"),
        }
        if choice := by_label.get(text.strip(". ")):
            axis, value = choice
            result[axis].add(value)
    return result


AMOUNT = r"\d{1,3}(?: \d{3})+(?:[,.]\d+)?|\d+(?:[,.]\d+)?"
SCALE = r"(?:тыс(?:яч\w*)?\.?|т\.\s*р\.?|тыр\.?)"
CURRENCY = r"₽|руб(?:л\w*)?\.?|rub\b|usd\b|\$|eur\b|€|доллар\w*|евро"
MONEY = re.compile(
    rf"(?<![\d:/.])(?P<edge>от\s+|до\s+)?(?P<low>{AMOUNT})"
    rf"(?P<low_scale>\s*{SCALE})?\s*"
    rf"(?:(?:[-–—]|до)\s*(?P<high>{AMOUNT})(?P<high_scale>\s*{SCALE})?\s*)?"
    rf"(?P<currency>{CURRENCY})?"
)
PAY_CUE = re.compile(r"зарплат|заработн\w* плат|оклад|оплат|вознаграждени|доход|денежное довольствие")
UNPAID = re.compile(
    r"\bнеоплачиваем\w*\s+(?:стажировк\w*|практик\w*|работ\w*)\b|"
    r"\b(?:работ\w*|стажировк\w*|практик\w*)\s+(?:без оплаты|не оплачивается)\b|^без оплаты[.!]?$"
)
SUPPLEMENT = re.compile(r"бонус\w*|преми\w*|компенсаци\w*|подъемн\w*|суточн\w*|матпомощ\w*")
PAY_UNIT = re.compile(r"(?:за\s+|в\s+|/\s*)(час\w*|смен\w*|месяц\w*|недел\w*|день|дня|сутки|год\w*|заказ\w*)")
PERIODS = {
    "час": "hour", "сме": "shift", "мес": "month", "нед": "week",
    "ден": "day", "дня": "day", "сут": "day", "год": "year", "зак": "order",
}
PAY_BASIS = {
    "net": re.compile(r"на руки|после (?:вычета|удержания)"),
    "gross": re.compile(r"до (?:вычета|удержания|налогов)|gross"),
}
INTRODUCTORY_PERIOD = r"(?:стажировк\w*|практик\w*|испытательн\w*\s+срок\w*)"
PAY_PHASES = {
    "after": re.compile(rf"\bпосле\s+{INTRODUCTORY_PERIOD}"),
    "introductory": re.compile(rf"\b(?:на|в период|во время)\s+{INTRODUCTORY_PERIOD}"),
}


@dataclass(frozen=True)
class Pay:
    low: Decimal | None
    high: Decimal | None
    unit: str = ""
    currency: str = ""
    basis: str = ""
    phase: str = ""


def positive_salary(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        amount = Decimal("".join(str(value).split()).replace(",", "."))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and amount > 0 else None


def currency_code(text):
    if text.startswith("руб") or text in {"rub", "₽", "rur"}:
        return "rub"
    if text.startswith("доллар") or text in {"usd", "$"}:
        return "usd"
    if text in {"eur", "€", "евро"}:
        return "eur"
    return text


def pay_from_text(text, structured=False):
    phase = next((key for key, pattern in PAY_PHASES.items() if pattern.search(text)), "")
    if unpaid := UNPAID.search(text):
        if re.search(INTRODUCTORY_PERIOD, unpaid[0]):
            phase = "introductory"
        return Pay(Decimal(0), Decimal(0), phase=phase)
    labeled = PAY_CUE.search(text)
    for match in MONEY.finditer(text):
        if (not (match["currency"] or match["low_scale"] or match["high_scale"])
                and not (structured or labeled)):
            continue
        # Exclude dates, hours, percentages and schedule ratios. A slash after currency may mean a pay period.
        after = text[match.end():].lstrip()
        if re.match(r"[:%]|\.\d|лет\b|раз\b|(?:час|год|месяц|недел)\w*\b", after):
            continue
        if not match["currency"] and re.match(r"[-–‑](?:я|й|е|го|му|ая|ый)\b|зарплат\w*\b", after):
            continue
        if after.startswith("/") and not (match["currency"] and PAY_UNIT.match(after)):
            continue
        # A sign-on bonus or travel reimbursement does not establish the wage.
        prefix = text[max(0, match.start() - 50):match.start()]
        supplements = list(SUPPLEMENT.finditer(prefix))
        wage_terms = list(PAY_CUE.finditer(prefix))
        if supplements and (not wage_terms or supplements[-1].start() > wage_terms[-1].start()):
            continue
        low = positive_salary(match["low"])
        high = positive_salary(match["high"])
        if low is None:
            continue
        if match["low_scale"] or match["high_scale"] and low < 1000:
            low *= 1000
        if high is not None and (match["high_scale"] or match["low_scale"] and high < 1000):
            high *= 1000
        unit = PAY_UNIT.search(text)
        details = (
            PERIODS[unit[1][:3]] if unit else "",
            currency_code(match["currency"] or ""),
            next((key for key, regex in PAY_BASIS.items() if regex.search(text)), ""),
            phase,
        )
        if high is not None:
            return Pay(low, high, *details)
        edge = (match["edge"] or "").strip()
        if edge == "от":
            return Pay(low, None, *details)
        if edge == "до":
            return Pay(None, low, *details)
        return Pay(low, low, *details)
    return None


def pay_conflict(first, second):
    if any(a and b and a != b for a, b in ((first.unit, second.unit),
                                          (first.currency, second.currency),
                                          (first.basis, second.basis),
                                          (first.phase, second.phase))):
        return False
    if not first.unit and second.unit not in {"", "month"}:
        return False
    if not second.unit and first.unit not in {"", "month"}:
        return False
    # Сумма «на руки» и границы без указания налоговой базы несопоставимы.
    if (first.basis == "net") != (second.basis == "net"):
        return False
    return ((first.high is not None and second.low is not None and first.high < second.low)
            or (second.high is not None and first.low is not None and second.high < first.low))
