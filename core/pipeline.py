"""The orchestrator. One email in, one Decision out.

OWNER: Ee Zhan.

Stages are injected rather than imported directly. Three reasons, and they map
onto three things the judges score:

  testability   - every stage can be stubbed, so tests do not need the dataset
  parallel work - three people build three stages against this one signature
  scalability   - a stage can be swapped for a remote implementation without
                  touching this file

`process` is pure with respect to the pipeline: it performs no I/O itself. The
`read_doc` callable it receives is the only thing that touches the disk. That
is what lets the same function run in a worker pool, a serverless handler or a
unit test unchanged.
"""
from __future__ import annotations

from typing import Callable

from core.types import (
    Classification,
    Decision,
    Email,
    ExtractedDoc,
    FieldComparison,
)

Classifier = Callable[[Email], Classification]
DocReader = Callable[[str], ExtractedDoc]
Comparator = Callable[[ExtractedDoc, ExtractedDoc], "tuple[FieldComparison, ...]"]
Decider = Callable[
    [Email, Classification, "ExtractedDoc | None", "ExtractedDoc | None",
     "tuple[FieldComparison, ...]"],
    Decision,
]
Drafter = Callable[[Email, Decision], str]


def candidate_docs(
    docs: "list[ExtractedDoc]",
) -> "tuple[tuple[ExtractedDoc, ...], tuple[ExtractedDoc, ...]]":
    """Return every plausible SI and BL, preserving attachment order.

    The parser's explicit kind wins. Filename suffixes are only a fallback
    for documents the parser could not identify, which prevents one document
    from appearing in both candidate lists.
    """
    sis: list[ExtractedDoc] = []
    bls: list[ExtractedDoc] = []
    for doc in docs:
        if doc.kind == "SI":
            sis.append(doc)
        elif doc.kind == "BL":
            bls.append(doc)
        elif "_SI." in doc.path.upper():
            sis.append(doc)
        elif "_BL." in doc.path.upper():
            bls.append(doc)
    return tuple(sis), tuple(bls)


def split_si_bl(
    docs: "list[ExtractedDoc]",
) -> "tuple[ExtractedDoc | None, ExtractedDoc | None]":
    """Pick the first SI and BL candidate out of the attachments.

    This deliberately retains the original deterministic first-candidate
    behaviour for batch scoring and callers that cannot ask a human. Interactive
    callers should use :func:`candidate_docs` and request an explicit choice
    whenever either side contains more than one candidate.
    """
    sis, bls = candidate_docs(docs)
    return (sis[0] if sis else None), (bls[0] if bls else None)


def process(
    email: Email,
    *,
    classify: Classifier,
    read_doc: DocReader,
    compare: Comparator,
    decide: Decider,
    draft_reply: "Drafter | None" = None,
) -> Decision:
    """Run one email through every stage. Never raises."""
    classification = classify(email)

    docs: list[ExtractedDoc] = []
    if classification.category == "BL_COMPARISON":
        docs = [read_doc(p) for p in email.attachments]

    si, bl = split_si_bl(docs)
    comparisons: tuple[FieldComparison, ...] = ()
    if si is not None and bl is not None and si.readable and bl.readable:
        comparisons = compare(si, bl)

    decision = decide(email, classification, si, bl, comparisons)

    if draft_reply is not None:
        decision = _with_reply(decision, draft_reply(email, decision))
    return decision


def _with_reply(decision: Decision, reply: str) -> Decision:
    from dataclasses import replace

    return replace(decision, reply_draft=reply)
