"""Evaluate classification against Zi Qi's hand-labelled development slice.

python -m eval.dev
python -m eval.dev --json
"""

from __future__ import annotations

import argparse
import json
import os

from adapters.inbox import LocalInbox
from core.classify import classify
from eval.metrics import CATEGORIES, evaluate, format_confusion_matrix


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LABELS = os.path.join(ROOT, "eval", "dev_labels.json")
DATA = os.path.join(ROOT, "data")


def run() -> dict[str, object]:
    with open(LABELS, encoding="utf-8") as fh:
        labels = json.load(fh)
    emails = {email.email_id: email for email in LocalInbox(DATA).emails()}

    rows: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for email_id, actual in labels.items():
        if actual not in CATEGORIES:
            raise ValueError(f"{email_id}: invalid dev label {actual!r}")
        if email_id not in emails:
            raise ValueError(f"{email_id}: dev label has no matching email")
        result = classify(emails[email_id], use_model=False)
        rows.append(
            {
                "actual": actual,
                "predicted": result.category,
                "decided_by": result.decided_by,
            }
        )
        if result.category != actual:
            errors.append(
                {
                    "email_id": email_id,
                    "actual": actual,
                    "predicted": result.category,
                    "evidence": result.evidence,
                }
            )

    report = evaluate(rows)
    report["errors"] = errors
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--json", action="store_true", help="print machine-readable JSON"
    )
    args = parser.parse_args()
    report = run()

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(format_confusion_matrix(report))
    print()
    print(f"n          {report['n']}")
    print(f"accuracy   {report['accuracy']:.4f}")
    print(f"macro-F1   {report['macro_f1']:.4f}")
    print(f"rule_pct   {report['rule_pct']:.1%}")
    if report["errors"]:
        print("\nmisclassifications:")
        for error in report["errors"]:
            print(
                f"  {error['email_id']}: {error['actual']} -> "
                f"{error['predicted']} ({error['evidence']})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
