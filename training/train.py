import argparse
import json
import math
from pathlib import Path
import sys

# Support invocation as a script from the repository root.
if __package__ in ("", None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.dataset import check_disjoint, load_dataset, topic_rows
from evaluation.linear_model import MAX_MODEL_BYTES, model_digest, probability
from report_storage import report_lock, write_report
from validation import TOPICS


def threshold_for(probabilities, expected):
    choices = sorted(set(probabilities))
    best = (0.0, 0.0, 0.0)
    for threshold in choices:
        predicted = [value >= threshold for value in probabilities]
        hits = sum(wanted and found for wanted, found in zip(expected, predicted))
        if hits and hits / sum(predicted) >= 0.8:
            best = max(best, (hits / sum(predicted), hits / sum(expected), threshold))
    return best[2] if best[0] else None


def train(train_rows, validation_rows):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion

    check_disjoint(train_rows, validation_rows)
    result = {"version": 1, "topics": {}}
    for topic in sorted(TOPICS):
        train_set, val_set = (topic_rows(rows, topic) for rows in (train_rows, validation_rows))
        if min(sum(item["label"] == label for item in train_set) for label in (0, 1)) < 2:
            raise ValueError(f"{topic}: для обучения нужно минимум два примера каждой метки.")
        branches = [
            ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), lowercase=False, max_features=50_000)),
            ("char", TfidfVectorizer(analyzer="char", ngram_range=(3, 5), lowercase=False, max_features=50_000)),
        ]
        union = FeatureUnion(branches)
        samples = union.fit_transform([row["text"] for row in train_set])
        estimator = LogisticRegression(max_iter=1000, random_state=0)
        estimator.fit(samples, [row["label"] for row in train_set])
        classifier = {"intercept": float(estimator.intercept_[0])}
        offset = 0
        for name, vectorizer in branches:
            classifier[name] = {
                gram: [float(vectorizer.idf_[index]), float(estimator.coef_[0, index + offset])]
                for gram, index in vectorizer.vocabulary_.items()
            }
            offset += len(vectorizer.vocabulary_)
        val_text = [row["text"] for row in val_set]
        scores = estimator.predict_proba(union.transform(val_text))[:, 1].tolist()
        if any(not math.isclose(score, probability(classifier, text), abs_tol=1e-9)
               for score, text in zip(scores, val_text)):
            raise ValueError(f"{topic}: экспорт stdlib расходится с прогнозом sklearn.")
        classifier["threshold"] = threshold_for(scores, [row["label"] for row in val_set])
        result["topics"][topic] = classifier
    return result


def main():
    parser = argparse.ArgumentParser(description="Обучить кандидат TF-IDF + Logistic Regression.")
    parser.add_argument("--train", default="datasets/train.jsonl")
    parser.add_argument("--validation", default="datasets/validation.jsonl")
    parser.add_argument("--output", type=Path, default=Path("model-candidate.json"))
    args = parser.parse_args()
    try:
        model = train(load_dataset(args.train, "train"), load_dataset(args.validation, "validation"))
        if args.output.resolve() in {Path(args.train).resolve(), Path(args.validation).resolve()}:
            raise ValueError("Файл модели должен отличаться от входных выборок.")
        document = {"sha256": model_digest(model), "model": model}
        if len(json.dumps(document, indent=2, ensure_ascii=False).encode()) + 1 > MAX_MODEL_BYTES:
            raise ValueError("Экспорт превышает допустимый размер модели.")
        with report_lock(args.output) as destination:
            write_report(destination, document)
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f"Обучение остановлено: {error}\n")


if __name__ == "__main__":
    main()
