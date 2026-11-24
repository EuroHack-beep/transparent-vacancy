import hashlib
import json
import math
import re
from collections import Counter

from evaluation.dataset import normalized_text
from validation import TOPICS, invalid_constant, unique_object


WORD = re.compile(r"(?u)\b\w\w+\b")
MAX_MODEL_BYTES = 24 * 1024 * 1024


def model_digest(model):
    data = json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def load_model(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_MODEL_BYTES + 1)
    if len(data) > MAX_MODEL_BYTES:
        raise ValueError("Превышен размер модели.")
    document = json.loads(data, parse_constant=invalid_constant, object_pairs_hook=unique_object)
    model = document["model"]
    if model["version"] != 1 or model_digest(model) != document["sha256"]:
        raise ValueError("Версия или контрольная сумма модели не совпала.")
    if set(model["topics"]) != TOPICS:
        raise ValueError("Некорректный список тем модели.")
    for classifier in model["topics"].values():
        threshold = classifier["threshold"]
        if not math.isfinite(classifier["intercept"]) or (threshold is not None and not 0 <= threshold <= 1):
            raise ValueError("Некорректные параметры классификатора.")
        for branch in ("word", "char"):
            weights = classifier[branch]
            if not isinstance(weights, dict) or len(weights) > 50_000 or any(
                not isinstance(gram, str) or len(gram) > 512 or not isinstance(pair, list) or
                len(pair) != 2 or not all(math.isfinite(x) for x in pair) or pair[0] <= 0
                for gram, pair in weights.items()
            ):
                raise ValueError("Некорректный словарь модели.")
    return model


def grams(text, kind):
    units = WORD.findall(text) if kind == "word" else text
    sizes = (1, 2) if kind == "word" else (3, 4, 5)
    for size in sizes:
        for offset in range(len(units) - size + 1):
            part = units[offset:offset + size]
            yield " ".join(part) if kind == "word" else part


def probability(classifier, text):
    text = normalized_text(text)
    score = classifier["intercept"]
    for kind in ("word", "char"):
        weights = classifier[kind]
        products = [(count * weights[gram][0], weights[gram][1])
                    for gram, count in Counter(grams(text, kind)).items() if gram in weights]
        norm = math.sqrt(sum(weight * weight for weight, _ in products))
        if norm:
            score += sum(weight * coefficient for weight, coefficient in products) / norm
    return 1 / (1 + math.exp(-score)) if score >= 0 else math.exp(score) / (1 + math.exp(score))
