from __future__ import annotations

import json

from eval.dev import LABELS, run
from eval.metrics import CATEGORIES, evaluate, format_confusion_matrix


MIN_MACRO_F1 = 0.95


def test_dev_slice_is_balanced_and_hand_labelled() -> None:
    with open(LABELS, encoding="utf-8") as fh:
        labels = json.load(fh)

    assert len(labels) == 80
    assert {
        category: list(labels.values()).count(category) for category in CATEGORIES
    } == {category: 16 for category in CATEGORIES}


def test_macro_f1_regression_gate() -> None:
    report = run()

    assert report["macro_f1"] >= MIN_MACRO_F1


def test_metrics_are_json_serialisable_and_matrix_is_printable() -> None:
    report = evaluate(
        [
            {"actual": "GENERAL", "predicted": "GENERAL", "decided_by": "rule"},
            {
                "actual": "SI_REQUEST",
                "predicted": "BL_COMPARISON",
                "decided_by": "model",
            },
        ]
    )

    assert json.loads(json.dumps(report))["n"] == 2
    rendered = format_confusion_matrix(report)
    assert "actual\\pred" in rendered
    assert "GEN" in rendered


def test_metrics_reject_unknown_categories() -> None:
    try:
        evaluate([{"actual": "UNKNOWN", "predicted": "GENERAL"}])
    except ValueError as error:
        assert "invalid actual category" in str(error)
    else:
        raise AssertionError("unknown category was accepted")
