import argparse
import json
from contextlib import nullcontext
from pathlib import Path
import sys
from time import perf_counter
import tracemalloc
from unittest.mock import patch

if __package__ in ("", None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.dataset import check_disjoint, load_dataset, topic_rows
from evaluation.linear_model import load_model, probability
from validation import TOPICS
from vacancy_checks import check_conditions


HEADINGS = {
    "job_title": "Должность", "duties": "Обязанности", "requirements": "Требования",
    "salary": "Оплата", "schedule": "График", "location": "Место работы",
    "employment": "Тип занятости",
}


def metrics(expected, predictions):
    hits = sum(y and p for y, p in zip(expected, predictions))
    false_positives = sum(not y and p for y, p in zip(expected, predictions))
    precision = hits / (hits + false_positives) if hits + false_positives else 0.0
    recall = hits / sum(expected)
    return {"precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "false_positives": false_positives}


def comparison(rows, classifier):
    active_model = nullcontext() if classifier is None else patch("vacancy_checks.classify", classifier)
    by_topic = {}
    start = perf_counter()
    tracemalloc.start()
    try:
        with active_model:
            for topic in sorted(TOPICS):
                examples = topic_rows(rows, topic)
                predictions = [
                    any(issue["topic"] == topic for issue in check_conditions({
                        "description": f'{HEADINGS[topic]}: {row["text"]}',
                    }))
                    for row in examples
                ]
                by_topic[topic] = metrics([row["label"] for row in examples], predictions)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {"topics": by_topic, "macro_f1": sum(x["f1"] for x in by_topic.values()) / len(by_topic),
            "macro_precision": sum(x["precision"] for x in by_topic.values()) / len(by_topic),
            "macro_recall": sum(x["recall"] for x in by_topic.values()) / len(by_topic),
            "false_positives": sum(x["false_positives"] for x in by_topic.values()),
            "python_allocations_peak_bytes": peak,
            "seconds": perf_counter() - start}


def main():
    parser = argparse.ArgumentParser(description="Сравнить методы на независимой размеченной выборке.")
    parser.add_argument("--train", default="datasets/train.jsonl")
    parser.add_argument("--validation", default="datasets/validation.jsonl")
    parser.add_argument("--test", default="datasets/test.jsonl")
    parser.add_argument("--model", type=Path, default=Path("model-candidate.json"))
    args = parser.parse_args()
    try:
        train = load_dataset(args.train, "train")
        validation = load_dataset(args.validation, "validation")
        test = load_dataset(args.test, "test")
        check_disjoint(train, validation)
        check_disjoint(train, test)
        check_disjoint(validation, test)
        model = load_model(args.model)["topics"]

        def linear(text, topic):
            classifier = model[topic]
            threshold = classifier["threshold"]
            return topic if threshold is not None and probability(classifier, text) >= threshold else None

        result = {"samples": len(test), "rules": comparison(test, lambda text, topic: None),
                  "rules+1-NN": comparison(test, None), "rules+linear": comparison(test, linear)}
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        parser.exit(1, f"Оценка остановлена: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
