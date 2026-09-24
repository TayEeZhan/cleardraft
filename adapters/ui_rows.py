"""Build the board/detail/stats rows the UI needs, from any inbox.

Shared by `scripts/export_ui_data.py` (the full 520-email batch snapshot,
`web/public/data.json`) and `api/_dataset.py` (a live per-request run over an
uploaded zip), so the two code paths can never drift into two different row
shapes. Everything here is a pure function of an `adapters.inbox.InboxSource`
plus a few small options - no network, no writing to disk, no import of
anything under `api/` or `scripts/`. This module is I/O-facing (it reads
attachments off disk through the inbox), which is why it lives in
`adapters/`, not `core/` - `core/` never touches the filesystem.

OWNER: whoever built the dataset-upload demo path.
"""
from __future__ import annotations

import collections
import os
import time
from typing import Callable, Mapping, Optional

from adapters.inbox import InboxSource
from core import pipeline
from core.classify import classify
from core.compare import compare
from core.decide import decide
from core.extract import extract
from core.recheck import recheck
from core.reply import FIELD_LABELS, _reference, draft_reply, draft_recheck_reply
from core.types import COMPARE_FIELDS


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


def _doc(doc, rel: Callable[[str], str]) -> "dict | None":
    if doc is None:
        return None
    return {
        "path": rel(doc.path),
        "kind": doc.kind,
        "readable": doc.readable,
        "error": doc.error,
        "text": doc.text,
    }


def _count_attachments(inbox: InboxSource) -> "int | None":
    """Best-effort count of attachment files under the inbox's own
    attachments/ folder, for the stats.totals.attachments number. Only
    `LocalInbox` exposes a filesystem `root`; any other InboxSource (e.g.
    HttpInbox) reports None rather than a wrong number."""
    root = getattr(inbox, "root", None)
    if not root:
        return None
    attachments_dir = os.path.join(root, "attachments")
    if not os.path.isdir(attachments_dir):
        return None
    total = 0
    for _dirpath, _dirnames, filenames in os.walk(attachments_dir):
        total += len(filenames)
    return total


def build_rows(
    inbox: InboxSource,
    *,
    rel: Callable[[str], str] = lambda p: p,
    amendments: "Optional[Mapping[str, str]]" = None,
    amendment_root: "Optional[str]" = None,
    max_emails: "Optional[int]" = None,
) -> dict:
    """Run every email in `inbox` through the pipeline and return the exact
    shape `web/public/data.json` carries at its top level: {generated_runtime_
    seconds, counts, board, detail, stats}. No "challenge" key - that is
    scripts/export_ui_data.py's own held-out-evidence merge, added by the
    caller, not part of the shared shape.

    `rel(path)` renders one document's path for display (e.g. relative to a
    data/ folder, or a bare basename) - callers control this so a published
    snapshot never carries a build machine's absolute paths.

    `amendments` (email_id -> a path amendment_root can resolve) and
    `amendment_root` together reproduce the "re-check an amended draft" demo
    block; omitted, every case's `recheck` is simply None, which is correct
    for an arbitrary uploaded dataset that has no hand-authored amendments.

    `max_emails`, when given, stops after that many emails from the inbox's
    own iteration order - the caller's own responsibility to cap an
    untrusted upload, kept here too as a second line of defence.
    """
    amendments = amendments or {}
    started = time.time()

    def read_doc(rel_path: str):
        return extract(inbox.attachment_path(rel_path))

    board: list[dict] = []
    detail: dict[str, dict] = {}
    cat_counts: collections.Counter = collections.Counter()
    defect_counts: collections.Counter = collections.Counter()
    escalations: collections.Counter = collections.Counter()
    decided: collections.Counter = collections.Counter()
    compared = 0

    for index, email in enumerate(inbox.emails()):
        if max_emails is not None and index >= max_emails:
            break

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
                "si": _fv(c.si, rel(si.path) if si else ""),
                "bl": _fv(c.bl, rel(bl.path) if bl else ""),
            })

        recheck_block = None
        amended = amendments.get(email.email_id)
        if amended and amendment_root and si is not None and bl is not None and si.readable and bl.readable:
            v2 = extract(os.path.join(amendment_root, amended))
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
            "documents": {"si": _doc(si, rel), "bl": _doc(bl, rel)},
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

    import adapters.model as model_mod

    return {
        "generated_runtime_seconds": round(runtime, 2),
        "counts": counts,
        "board": board,
        "detail": detail,
        "stats": {
            "totals": {
                "emails": total,
                "compared": compared,
                "defects_found": counts["mismatch"],
                "attachments": _count_attachments(inbox),
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


__all__ = ["build_rows"]
