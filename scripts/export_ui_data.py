"""Run the pipeline once and write everything the UI needs as one JSON file.

    python scripts/export_ui_data.py

Why this exists: the web UI reads `web/public/data.json` so it renders with no
backend at all. When the API is live the UI prefers it and falls back to this
file, which means the demo cannot break in front of a judge because a free-tier
server was cold.

The shape here matches `docs/API.md` exactly, so swapping the fetch URL from
this file to `/api/...` needs no change in the UI.
"""
from __future__ import annotations

import collections
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters import model as model_mod                       # noqa: E402
from adapters.inbox import LocalInbox                         # noqa: E402
from core import pipeline                                     # noqa: E402
from core.classify import classify                            # noqa: E402
from core.compare import compare                              # noqa: E402
from core.decide import decide                                # noqa: E402
from core.extract import extract                              # noqa: E402
from core.recheck import recheck                              # noqa: E402
from core.reply import FIELD_LABELS, _reference, draft_reply, draft_recheck_reply  # noqa: E402
from core.types import COMPARE_FIELDS                         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Hand-authored amended drafts. See data/demo/README.md - demo only, never scored.
_manifest = os.path.join(ROOT, "data", "demo", "amendments.json")
AMENDMENTS: dict = (
    json.load(open(_manifest, encoding="utf-8")).get("amendments", {})
    if os.path.exists(_manifest) else {}
)


def _rel(path: str) -> str:
    """Path relative to data/, forward slashes. The snapshot is published, so
    it must never carry the absolute path of the machine that built it."""
    if not path:
        return ""
    return os.path.relpath(path, os.path.join(ROOT, "data")).replace(os.sep, "/")


def _fv(field_value, source: str) -> "dict | None":
    if field_value is None:
        return None
    return {
        "value": field_value.value,
        "raw": field_value.raw,
        "line_no": field_value.line_no,
        "label": field_value.label,
        "decided_by": field_value.decided_by,
        "source": source,
    }


def _doc(doc) -> "dict | None":
    if doc is None:
        return None
    return {
        "path": _rel(doc.path),
        "kind": doc.kind,
        "readable": doc.readable,
        "error": doc.error,
        "text": doc.text,
    }


def main() -> int:
    started = time.time()
    inbox = LocalInbox(os.path.join(ROOT, "data"))

    def read_doc(rel: str):
        return extract(inbox.attachment_path(rel))

    board: list[dict] = []
    detail: dict[str, dict] = {}
    cat_counts: collections.Counter = collections.Counter()
    defect_counts: collections.Counter = collections.Counter()
    escalations: collections.Counter = collections.Counter()
    decided: collections.Counter = collections.Counter()
    compared = 0

    for email in inbox.emails():
        classification = classify(email)
        docs = []
        if classification.category == "BL_COMPARISON":
            docs = [read_doc(p) for p in email.attachments]
        si, bl = pipeline.split_si_bl(docs)

        comparisons = ()
        if si is not None and bl is not None and si.readable and bl.readable:
            comparisons = compare(si, bl)
            compared += 1

        decision = decide(email, classification, si, bl, comparisons)
        reply = draft_reply(email, decision)
        reference = _reference(email)

        cat_counts[decision.category] += 1
        decided[decision.decided_by] += 1
        for f in decision.defect_fields:
            defect_counts[f] += 1
        if decision.review_reason:
            escalations[decision.review_reason] += 1

        board.append({
            "email_id": email.email_id,
            "subject": email.subject,
            "from": email.sender,
            "reference": reference,
            "category": decision.category,
            "status": decision.status,
            "review_reason": decision.review_reason,
            "has_defect": decision.has_defect,
            "defect_fields": list(decision.defect_fields),
            "attachment_count": len(email.attachments),
            "decided_by": decision.decided_by,
        })

        rows = []
        for c in comparisons:
            rows.append({
                "field": c.field,
                "label": FIELD_LABELS.get(c.field, c.field),
                "matched": c.matched,
                "undecidable": c.undecidable,
                "si": _fv(c.si, _rel(si.path) if si else ""),
                "bl": _fv(c.bl, _rel(bl.path) if bl else ""),
            })

        recheck_block = None
        amended = AMENDMENTS.get(email.email_id)
        if amended and si is not None and bl is not None and si.readable and bl.readable:
            v2 = extract(os.path.join(ROOT, "data", amended))
            rrows = recheck(si, bl, v2)
            recheck_block = {
                "source": amended,
                "demo": True,
                "rows": [{
                    "field": r.field,
                    "label": FIELD_LABELS.get(r.field, r.field),
                    "outcome": r.outcome,
                    "v1": r.v1.bl.value if r.v1.bl else None,
                    "v2": r.v2.bl.value if r.v2.bl else None,
                    "si": r.v2.si.value if r.v2.si else None,
                } for r in rrows],
                "reply_draft": draft_recheck_reply(email, rrows),
            }

        detail[email.email_id] = {
            "email_id": email.email_id,
            "subject": email.subject,
            "from": email.sender,
            "body": email.body,
            "reference": reference,
            "category": decision.category,
            "intent": classification.intent,
            "evidence": classification.evidence,
            "confidence": classification.confidence,
            "status": decision.status,
            "review_reason": decision.review_reason,
            "rationale": decision.rationale,
            "decided_by": decision.decided_by,
            "defect_fields": list(decision.defect_fields),
            "documents": {"si": _doc(si), "bl": _doc(bl)},
            "comparisons": rows,
            "reply_draft": reply,
            "recheck": recheck_block,
        }

    runtime = time.time() - started
    total = len(board)
    counts = {
        "mismatch": sum(1 for b in board if b["status"] == "MISMATCH"),
        "needs_review": sum(1 for b in board if b["status"] == "NEEDS_REVIEW"),
        "cleared": sum(
            1 for b in board
            if b["status"] == "OK" and b["category"] == "BL_COMPARISON"
        ),
        "other": sum(1 for b in board if b["category"] != "BL_COMPARISON"),
    }

    payload = {
        "generated_runtime_seconds": round(runtime, 2),
        "counts": counts,
        "board": board,
        "detail": detail,
        "stats": {
            "totals": {
                "emails": total,
                "compared": compared,
                "defects_found": counts["mismatch"],
                "attachments": 250,
            },
            "categories": dict(cat_counts),
            "defect_fields": {
                f: defect_counts.get(f, 0) for f in COMPARE_FIELDS
            },
            "escalations": dict(escalations),
            "decisions": {
                "by_rule": decided.get("rule", 0),
                "by_model": decided.get("model", 0),
                "rule_pct": (decided.get("rule", 0) / total) if total else 0.0,
            },
            "model": {
                "available": model_mod.available(),
                **model_mod.STATS.as_dict(),
            },
            "runtime_seconds": round(runtime, 2),
        },
    }

    # Held-out evidence for the model tier, if scripts/run_challenge.py has run.
    challenge_path = os.path.join(ROOT, "out", "challenge_report.json")
    if os.path.exists(challenge_path):
        ch = json.load(open(challenge_path, encoding="utf-8"))
        e = ch["emails"]
        payload["stats"]["challenge"] = {
            "model": ch["model"],
            "emails": e["n"],
            "category_accuracy": e["category_accuracy"],
            "intent_accuracy": e["intent_accuracy"],
            "decided_by_model": e["decided_by_model"],
            "rescued_by_model": e["rescued_by_model"],
            "model_wrong": e["model_wrong"],
            "doc_pairs": [{
                "pair": p["pair"],
                "fields_rules": p["rules_only"]["fields_found"]["si"] + p["rules_only"]["fields_found"]["bl"],
                "fields_model": p["with_model"]["fields_found"]["si"] + p["with_model"]["fields_found"]["bl"],
                "fields_present": p["with_model"]["fields_present"]["si"] + p["with_model"]["fields_present"]["bl"],
                "defects_found": p["with_model"]["defects"],
                "defects_gold": p["gold_defects"],
            } for p in ch["docs"]["pairs"]],
            "calls": ch["model_stats"]["calls"],
            "tokens": ch["model_stats"]["input_tokens"] + ch["model_stats"]["output_tokens"],
            "gate_rejections": ch["model_stats"]["gate_rejections"],
        }

    out = os.path.join(ROOT, "web", "public", "data.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))

    size_mb = os.path.getsize(out) / 1_048_576
    print(f"wrote {out}")
    print(f"  {total} emails, {compared} compared, {runtime:.2f}s, {size_mb:.2f} MB")
    print(f"  board tabs: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
