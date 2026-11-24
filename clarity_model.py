"""Local nearest-neighbor classifier for phrases in Russian vacancies."""
from functools import lru_cache
import json
from pathlib import Path
import math
import re


def baseline_examples():
    path = Path(__file__).with_name("datasets") / "baseline.jsonl"
    examples = {}
    with path.open(encoding="utf-8") as dataset:
        for line in dataset:
            row = json.loads(line)
            examples.setdefault(row["topic"], []).append(row["text"])
    return examples


EXAMPLES = baseline_examples()



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


def classify(phrase, topic=None):
    sample = features(phrase)
    if not sample:
        return None
    labels = ("other", topic) if topic else EXAMPLES
    scores = dict.fromkeys(labels, 0.0)
    for label, training in fitted_examples():
        if label not in scores:
            continue
        similarity = len(sample & training) / math.sqrt(len(sample) * len(training))
        scores[label] = max(scores[label], similarity)
    best, second = sorted(scores, key=scores.get, reverse=True)[:2]
    if best != "other" and scores[best] >= 0.66 and scores[best] - scores[second] >= 0.10:
        return best
    return None
