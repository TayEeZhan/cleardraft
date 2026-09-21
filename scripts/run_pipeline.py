"""Run every email through the pipeline and write submission.json.

    python scripts/run_pipeline.py --data data --out out/submission.json

Or, against the organiser's docker server instead of a local folder:

    python scripts/run_pipeline.py --server http://localhost:8080 --out out/submission.json

`--data` and `--server` are mutually exclusive. Neither given falls back to
`--data data`, unchanged from before `--server` existed.

Stages that are still stubs fall back to a SAFE DEFAULT (GENERAL / OK) and are
counted in the summary. That is deliberate: the plumbing is provably correct
from hour one, and each owner's progress shows up as the stub count dropping.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.inbox import HttpInbox, InboxSource, LocalInbox  # noqa: E402
from core import pipeline                                    # noqa: E402
from core.types import (                                     # noqa: E402
    Classification,
    Decision,
    Email,
    ExtractedDoc,
    FieldComparison,
)

STUBBED: dict[str, int] = {}


def _note(stage: str) -> None:
    STUBBED[stage] = STUBBED.get(stage, 0) + 1


# -- safe fallbacks -------------------------------------------------------
def _fallback_classify(email: Email) -> Classification:
    try:
        from core.classify import classify

        return classify(email)
    except NotImplementedError:
        _note("classify")
        return Classification(
            category="GENERAL", intent="unknown", decided_by="rule",
            confidence=0.0, evidence="stub",
        )


def _make_reader(inbox: InboxSource):
    def read(rel: str) -> ExtractedDoc:
        try:
            from core.extract import extract

            return extract(inbox.attachment_path(rel))
        except NotImplementedError:
            _note("extract")
            return ExtractedDoc(
                path=rel, kind="OTHER", readable=False, error="stub",
            )

    return read


def _fallback_compare(
    si: ExtractedDoc, bl: ExtractedDoc
) -> "tuple[FieldComparison, ...]":
    try:
        from core.compare import compare

        return compare(si, bl)
    except NotImplementedError:
        _note("compare")
        return ()


def _fallback_decide(
    email: Email,
    classification: Classification,
    si: "ExtractedDoc | None",
    bl: "ExtractedDoc | None",
    comparisons: "tuple[FieldComparison, ...]",
) -> Decision:
    try:
        from core.decide import decide

        return decide(email, classification, si, bl, comparisons)
    except NotImplementedError:
        _note("decide")
        return Decision(
            email_id=email.email_id,
            category=classification.category,
            status="OK",
            review_reason=None,
            has_defect=False,
            defect_fields=(),
            decided_by="rule",
            rationale="stub",
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group()
    group.add_argument(
        "--data", default=None,
        help="local data directory with inbox/ + attachments/ (default: 'data')",
    )
    group.add_argument(
        "--server", default=None,
        help="organiser HTTP server base URL, e.g. http://localhost:8080",
    )
    ap.add_argument("--out", default="out/submission.json")
    args = ap.parse_args()

    inbox: InboxSource
    if args.server:
        inbox = HttpInbox(args.server)
    else:
        inbox = LocalInbox(args.data or "data")
    read_doc = _make_reader(inbox)

    started = time.time()
    submission: dict[str, dict] = {}
    for email in inbox.emails():
        decision = pipeline.process(
            email,
            classify=_fallback_classify,
            read_doc=read_doc,
            compare=_fallback_compare,
            decide=_fallback_decide,
        )
        submission[email.email_id] = decision.to_submission()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(submission, fh, indent=2)

    elapsed = time.time() - started
    print(f"wrote {len(submission)} records to {args.out} in {elapsed:.2f}s")
    if STUBBED:
        print("stubs still in play:")
        for stage, n in sorted(STUBBED.items()):
            print(f"   {stage:<10} {n:>5} calls")
    else:
        print("no stubs - every stage is implemented")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
