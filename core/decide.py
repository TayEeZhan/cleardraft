"""Stage 4: turn comparisons into the organiser's verdict.

OWNER: Ee Zhan.

PRECEDENCE. Escalation beats everything. We would rather hand a clerk a case
we could not decide than hand them a confident wrong answer.

  1. category is not BL_COMPARISON        -> OK, nothing to compare
  2. intent is "compare" and fewer than
     two attachments                      -> NEEDS_REVIEW / missing_attachment
  3. either document is unreadable        -> NEEDS_REVIEW / unreadable
  4. either document is not an SI or BL   -> NEEDS_REVIEW / wrong_doc_type
  5. any required field blank or missing  -> NEEDS_REVIEW / missing_value
  6. any field mismatched                 -> MISMATCH + defect_fields
  7. otherwise                            -> OK

RULE 2 IS THE SUBTLE ONE. A BL_COMPARISON email whose intent is "send_doc"
and which has no attachments is OK, not an escalation. 91 of the 520 emails
are exactly that case. Escalating them would drop escalation precision from
about 1.0 to about 0.05.
"""
from __future__ import annotations

from core.types import Classification, Decision, Email, ExtractedDoc, FieldComparison


def decide(
    email: Email,
    classification: Classification,
    si: "ExtractedDoc | None",
    bl: "ExtractedDoc | None",
    comparisons: "tuple[FieldComparison, ...]",
) -> Decision:
    """Apply the precedence ladder above and return on the first hit.

    Must never raise: this is the last stage before a person reads the
    output, and a crash here is worse than a wrong-but-visible decision.
    """
    try:
        return _decide(email, classification, si, bl, comparisons)
    except Exception as exc:  # pragma: no cover - defence of last resort
        return Decision(
            email_id=email.email_id,
            category=classification.category if classification else "GENERAL",
            status="NEEDS_REVIEW",
            review_reason="unreadable",
            has_defect=False,
            defect_fields=(),
            decided_by="rule",
            comparisons=(),
            rationale=f"decide() raised {exc.__class__.__name__}: {exc}",
        )


def _decided_by(classification: Classification) -> "str":
    return "model" if classification.decided_by == "model" else "rule"


def _decide(
    email: Email,
    classification: Classification,
    si: "ExtractedDoc | None",
    bl: "ExtractedDoc | None",
    comparisons: "tuple[FieldComparison, ...]",
) -> Decision:
    decided_by = _decided_by(classification)

    # 1. Not a comparison email at all -> nothing to compare.
    if classification.category != "BL_COMPARISON":
        return Decision(
            email_id=email.email_id,
            category=classification.category,
            status="OK",
            review_reason=None,
            has_defect=False,
            defect_fields=(),
            decided_by=decided_by,
            comparisons=comparisons,
            rationale="category is not BL_COMPARISON, nothing to compare",
        )

    # 2. THE TRAP. Only an explicit request to compare escalates on missing
    # attachments. "Please send the draft BL" with no attachments is OK.
    if classification.intent == "compare" and len(email.attachments) < 2:
        return Decision(
            email_id=email.email_id,
            category=classification.category,
            status="NEEDS_REVIEW",
            review_reason="missing_attachment",
            has_defect=False,
            defect_fields=(),
            decided_by=decided_by,
            comparisons=comparisons,
            rationale=(
                "comparison requested but only "
                f"{len(email.attachments)} of 2 documents attached"
            ),
        )

    if classification.intent != "compare":
        return Decision(
            email_id=email.email_id,
            category=classification.category,
            status="OK",
            review_reason=None,
            has_defect=False,
            defect_fields=(),
            decided_by=decided_by,
            comparisons=comparisons,
            rationale=f"intent is {classification.intent!r}, nothing to compare yet",
        )

    # 3. Either document failed to read.
    if si is None or bl is None or si.readable is False or bl.readable is False:
        return Decision(
            email_id=email.email_id,
            category=classification.category,
            status="NEEDS_REVIEW",
            review_reason="unreadable",
            has_defect=False,
            defect_fields=(),
            decided_by=decided_by,
            comparisons=comparisons,
            rationale="one or both documents could not be read",
        )

    # 4. Wrong document type.
    if si.kind != "SI" or bl.kind != "BL":
        return Decision(
            email_id=email.email_id,
            category=classification.category,
            status="NEEDS_REVIEW",
            review_reason="wrong_doc_type",
            has_defect=False,
            defect_fields=(),
            decided_by=decided_by,
            comparisons=comparisons,
            rationale=(
                f"expected SI+BL, got {si.kind}+{bl.kind}"
            ),
        )

    # 5. Any row undecidable -> escalate.
    undecidable_fields = tuple(sorted(c.field for c in comparisons if c.undecidable))
    if undecidable_fields:
        return Decision(
            email_id=email.email_id,
            category=classification.category,
            status="NEEDS_REVIEW",
            review_reason="missing_value",
            has_defect=False,
            defect_fields=(),
            decided_by=decided_by,
            comparisons=comparisons,
            rationale=(
                f"{len(undecidable_fields)} field(s) missing or blank: "
                + ", ".join(undecidable_fields)
            ),
        )

    # 6. Genuine mismatches.
    defect_fields = tuple(
        sorted(c.field for c in comparisons if not c.matched and not c.undecidable)
    )
    if defect_fields:
        return Decision(
            email_id=email.email_id,
            category=classification.category,
            status="MISMATCH",
            review_reason=None,
            has_defect=True,
            defect_fields=defect_fields,
            decided_by=decided_by,
            comparisons=comparisons,
            rationale=(
                f"{len(defect_fields)} of {len(comparisons)} fields differ: "
                + ", ".join(defect_fields)
            ),
        )

    # 7. Clean.
    return Decision(
        email_id=email.email_id,
        category=classification.category,
        status="OK",
        review_reason=None,
        has_defect=False,
        defect_fields=(),
        decided_by=decided_by,
        comparisons=comparisons,
        rationale=f"all {len(comparisons)} fields match" if comparisons else "no discrepancies found",
    )
