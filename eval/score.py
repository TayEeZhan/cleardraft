"""Score a submission against the organiser's scorer.

The ground truth is NOT in this repository and must never be. See PLAN.md
section 8. This wrapper loads it from a gitignored path so the number is
measurable without the labels ever entering version control.

    python -m eval.score out/submission.json
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

SECRETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".secrets")
GROUND_TRUTH = os.path.join(SECRETS, "ground_truth.json")
SCORER = os.path.join(SECRETS, "organiser_scoring.py")


def _load_scorer():
    spec = importlib.util.spec_from_file_location("organiser_scoring", SCORER)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(SCORER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m eval.score <submission.json>", file=sys.stderr)
        return 2
    if not os.path.exists(GROUND_TRUTH):
        print(
            "No ground truth at .secrets/ground_truth.json.\n"
            "That is expected on a clean clone - the labels are\n"
            "deliberately not in this repository. See PLAN.md section 8.",
            file=sys.stderr,
        )
        return 1

    with open(GROUND_TRUTH, encoding="utf-8") as fh:
        truth = json.load(fh)
    with open(argv[1], encoding="utf-8") as fh:
        sub = json.load(fh)

    result = _load_scorer().score_all(truth, sub)

    s1, s3 = result["stage1"], result["stage3"]
    rel, e2e = result["reliability"], result["end_to_end"]
    rule_pct = s1.get("rule_pct")

    print("=" * 58)
    print(f"  FINAL SCORE            {result['final_score']:.4f}")
    print("=" * 58)
    print(f"  stage1 accuracy        {s1['accuracy']:.4f}")
    print(f"  stage1 macro F1        {s1['macro_f1']:.4f}   (weight 0.30)")
    print(f"  stage3 defect F1       {s3['defect_f1']:.4f}   (weight 0.20)")
    print(f"  end-to-end rate        {e2e['rate']:.4f}   (weight 0.50)")
    print(f"                         {e2e['success']}/{e2e['total']} defect emails caught")
    print("-" * 58)
    print(f"  field F1               {s3['field_f1']:.4f}")
    print(f"  exact match rate       {s3['exact_match_rate']:.4f}")
    print(f"  escalation recall      {rel['escalation_recall']:.4f}")
    print(f"  escalation precision   {rel['escalation_precision']:.4f}")
    if rule_pct is not None:
        print(f"  decided by rule        {rule_pct:.1%}")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
