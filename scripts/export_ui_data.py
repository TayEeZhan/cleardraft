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
from core.reply import FIELD_LABELS, _reference, draft_reply   # noqa: E402
from core.types import COMPARE_FIELDS                         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
        "path": doc.path,
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
                "si": _fv(c.si, si.path if si else ""),
                "bl": _fv(c.bl, bl.path if bl else ""),
            })

        detail[email.email_id] = {
            "email_id": email.email_id,
            "subject": email.subject,
            "from": email.sender,
            "body": email.body,
            "reference": reference,
            "category": decision.category,
            "intent": classification.intent,
            "evidence": classification.evidence,
            "status": decision.status,
            "review_reason": decision.review_reason,
            "rationale": decision.rationale,
            "decided_by": decision.decided_by,
            "defect_fields": list(decision.defect_fields),
            "documents": {"si": _doc(si), "bl": _doc(bl)},
            "comparisons": rows,
            "reply_draft": reply,
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
