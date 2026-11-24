"""Local nearest-neighbor classifier for phrases in Russian vacancies."""
from functools import lru_cache
import math
import re


EXAMPLES = {
    "schedule": (
        "гибкий график", "гибкий режим работы", "индивидуальный график",
        "график по договоренности", "график обсуждается на собеседовании",
        "часы работы согласуем индивидуально", "смены согласуются отдельно",
        "график устанавливается по согласованию сторон",
    ),
    "salary": (
        "конкурентная зарплата", "конкурентная заработная плата",
        "достойная оплата труда", "зарплата по договоренности",
        "уровень дохода обсудим на собеседовании", "оклад по результатам собеседования",
        "заработная плата обсуждается индивидуально",
    ),
    "other": (
        "полный рабочий день", "неполный рабочий день", "сменный график два через два",
        "график пять дней в неделю", "оплата почасовая 350 рублей",
        "заработная плата от 50000 рублей", "проверять график производства",
        "вести учет заработной платы", "составлять график отпусков",
        "гибкий подход к решению задач", "знание графических программ",
        "выплачивать заработную плату", "разработка графиков работы сотрудников",
    ),
}


def features(text):
    words = re.findall(r"[а-яa-z0-9]+", text.casefold().replace("ё", "е"))
    grams = set()
    for word in words:
        padded = " " + word + " "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


@lru_cache(maxsize=1)
def fitted_examples():
    return [(label, features(phrase)) for label, phrases in EXAMPLES.items() for phrase in phrases]


def classify(phrase):
    sample = features(phrase)
    if not sample:
        return None
    scores = {label: 0.0 for label in EXAMPLES}
    for label, training in fitted_examples():
        similarity = len(sample & training) / math.sqrt(len(sample) * len(training))
        scores[label] = max(scores[label], similarity)
    best, second = sorted(scores, key=scores.get, reverse=True)[:2]
    if best != "other" and scores[best] >= 0.66 and scores[best] - scores[second] >= 0.10:
        return best
    return None
