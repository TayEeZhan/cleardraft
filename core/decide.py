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
    # TODO(ee-zhan): implement the precedence ladder above.
    raise NotImplementedError
