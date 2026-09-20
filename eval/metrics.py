"""JSON-serialisable Stage-1 metrics for the five organiser categories."""

from __future__ import annotations

from collections.abc import Iterable, Mapping


CATEGORIES = (
    "BL_COMPARISON",
    "SI_REQUEST",
    "INVOICE_QUERY",
    "GENERAL",
    "SPAM",
)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate(rows: Iterable[Mapping[str, str]]) -> dict[str, object]:
    """Return accuracy, macro-F1, per-category metrics, matrix and rule_pct."""
    matrix = {
        actual: {predicted: 0 for predicted in CATEGORIES} for actual in CATEGORIES
    }
    total = correct = rule_count = sourced_count = 0

    for index, row in enumerate(rows):
        actual = row.get("actual")
        predicted = row.get("predicted")
        if actual not in CATEGORIES:
            raise ValueError(f"row {index}: invalid actual category {actual!r}")
        if predicted not in CATEGORIES:
            raise ValueError(f"row {index}: invalid predicted category {predicted!r}")
        matrix[actual][predicted] += 1
        total += 1
        correct += actual == predicted

        decided_by = row.get("decided_by")
        if decided_by:
            if decided_by not in ("rule", "model"):
                raise ValueError(f"row {index}: invalid decided_by {decided_by!r}")
            sourced_count += 1
            rule_count += decided_by == "rule"

    per_category: dict[str, dict[str, float | int]] = {}
    f1_sum = 0.0
    for category in CATEGORIES:
        tp = matrix[category][category]
        fp = sum(
            matrix[actual][category] for actual in CATEGORIES if actual != category
        )
        fn = sum(
            matrix[category][predicted]
            for predicted in CATEGORIES
            if predicted != category
        )
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        f1 = _ratio(2 * precision * recall, precision + recall)
        f1_sum += f1
        per_category[category] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": sum(matrix[category].values()),
            "tp": tp,
            "fp": fp,
            "fn": fn,
        }

    return {
        "categories": list(CATEGORIES),
        "n": total,
        "accuracy": _ratio(correct, total),
        "macro_f1": f1_sum / len(CATEGORIES),
        "rule_pct": _ratio(rule_count, sourced_count) if sourced_count else None,
        "confusion_matrix": matrix,
        "per_category": per_category,
    }


def format_confusion_matrix(report: Mapping[str, object]) -> str:
    """Render actual rows against predicted columns as a compact table."""
    matrix = report.get("confusion_matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("report has no confusion_matrix")
    short = ("BL", "SI", "INV", "GEN", "SPAM")
    lines = ["actual\\pred " + " ".join(f"{label:>5}" for label in short)]
    for actual, label in zip(CATEGORIES, short):
        row = matrix.get(actual)
        if not isinstance(row, Mapping):
            raise ValueError(f"matrix row {actual} is missing")
        values = [row.get(predicted) for predicted in CATEGORIES]
        if not all(isinstance(value, int) for value in values):
            raise ValueError(f"matrix row {actual} contains a non-integer")
        lines.append(f"{label:>11} " + " ".join(f"{value:>5}" for value in values))
    return "\n".join(lines)
