"""Measure what the model tier actually adds, on data the rules have never seen.

    python scripts/run_challenge.py

The organiser corpus is fully covered by the rule tier, so on it the model is
never consulted and "the AI earns its place" is unproven. This runs a
HELD-OUT set (data/challenge/, hand-written without reference to our rules)
twice - rules only, then rules + model - and reports the difference:

  classification  accuracy with and without the model, and how many emails the
                  rules declined that the model then got right (or wrong)
  extraction      fields found with and without the model, on documents using
                  labels the alias table does not know; how many model answers
                  the verification gate threw away
  comparison      whether the planted differences are caught end to end

Every run spends real money (Haiku 4.5, a few cents). Results are written to
out/challenge_report.json and folded into the UI's accuracy screen.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters import model                          # noqa: E402
from core.classify import classify                  # noqa: E402
from core.compare import compare                    # noqa: E402
from core.extract import extract                    # noqa: E402
from core.types import COMPARE_FIELDS, Email        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH = os.path.join(ROOT, "data", "challenge")


def run_emails() -> dict:
    rows = []
    with open(os.path.join(CH, "emails.jsonl"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))

    out = []
    for r in rows:
        email = Email(email_id=r["id"], sender="challenge@example.com",
                      subject=r["subject"], body=r["body"], attachments=())
        rule = classify(email, use_model=False)
        full = classify(email, use_model=True)
        out.append({
            "id": r["id"],
            "subject": r["subject"],
            "gold": [r["gold_category"], r["gold_intent"]],
            "rules": [rule.category, rule.intent, rule.decided_by],
            "with_model": [full.category, full.intent, full.decided_by, full.evidence],
        })

    def acc(key: str, idx: int) -> float:
        return sum(1 for o in out if o[key][idx] == o["gold"][idx]) / len(out)

    rescued = [o for o in out
               if o["with_model"][2] == "model"
               and o["with_model"][0] == o["gold"][0]
               and o["rules"][0] != o["gold"][0]]
    model_wrong = [o for o in out
                   if o["with_model"][2] == "model" and o["with_model"][0] != o["gold"][0]]
    return {
        "n": len(out),
        "category_accuracy": {"rules_only": acc("rules", 0), "with_model": acc("with_model", 0)},
        "intent_accuracy": {"rules_only": acc("rules", 1), "with_model": acc("with_model", 1)},
        "decided_by_model": sum(1 for o in out if o["with_model"][2] == "model"),
        "rescued_by_model": len(rescued),
        "model_wrong": len(model_wrong),
        "rows": out,
    }


def run_docs() -> dict:
    gold = json.load(open(os.path.join(CH, "docs", "gold.json"), encoding="utf-8"))
    pairs = []
    for name, g in gold.items():
        si_path = os.path.join(CH, "docs", f"{name}_SI.txt")
        bl_path = os.path.join(CH, "docs", f"{name}_BL.txt")
        entry = {"pair": name, "gold_defects": sorted(g.get("defect_fields") or [])}
        for mode, use in (("rules_only", False), ("with_model", True)):
            si = extract(si_path, use_model=use)
            bl = extract(bl_path, use_model=use)
            rows = compare(si, bl)
            found = {
                side: sum(1 for f in COMPARE_FIELDS if f in d.fields and d.fields[f].value)
                for side, d in (("si", si), ("bl", bl))
            }
            expected = {
                side: sum(1 for f in COMPARE_FIELDS if g[side].get(f))
                for side in ("si", "bl")
            }
            by_model = sum(1 for d in (si, bl) for v in d.fields.values()
                           if v.decided_by == "model")
            entry[mode] = {
                "fields_found": found,
                "fields_present": expected,
                "by_model": by_model,
                "defects": sorted(r.field for r in rows if not r.matched and not r.undecidable),
                "undecidable": sorted(r.field for r in rows if r.undecidable),
            }
        pairs.append(entry)
    return {"pairs": pairs}


def main() -> int:
    if not model.available():
        print("No model available - set ANTHROPIC_API_KEY in .env and do not set "
              "CLEARDRAFT_USE_MODEL=0. This script exists to exercise the model.")
        return 1
    started = time.time()
    emails = run_emails()
    docs = run_docs()
    report = {
        "model": model.MODEL,
        "emails": emails,
        "docs": docs,
        "model_stats": model.STATS.as_dict(),
        "seconds": round(time.time() - started, 1),
    }
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    with open(os.path.join(ROOT, "out", "challenge_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    e = emails
    print(f"HELD-OUT EMAILS ({e['n']})")
    print(f"  category accuracy   rules only {e['category_accuracy']['rules_only']:.0%}"
          f"   with model {e['category_accuracy']['with_model']:.0%}")
    print(f"  intent accuracy     rules only {e['intent_accuracy']['rules_only']:.0%}"
          f"   with model {e['intent_accuracy']['with_model']:.0%}")
    print(f"  decided by model {e['decided_by_model']}  |  rescued {e['rescued_by_model']}"
          f"  |  model wrong {e['model_wrong']}")
    print("HELD-OUT DOCUMENTS")
    for p in docs["pairs"]:
        r, m = p["rules_only"], p["with_model"]
        print(f"  {p['pair']}: fields SI {r['fields_found']['si']}->{m['fields_found']['si']}"
              f"/{m['fields_present']['si']}  BL {r['fields_found']['bl']}->{m['fields_found']['bl']}"
              f"/{m['fields_present']['bl']}  | defects {m['defects']} (gold {p['gold_defects']})"
              f"  undecidable {m['undecidable']}")
    s = report["model_stats"]
    print(f"MODEL  calls {s['calls']}  tokens {s['input_tokens']}+{s['output_tokens']}"
          f"  gate rejections {s['gate_rejections']}  failures {s['failures']}  ({report['seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
