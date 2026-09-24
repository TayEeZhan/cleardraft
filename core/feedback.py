"""Pure rules for applying a clerk's review to a displayed report.

Human feedback is an overlay: it may make a previously decisive result safer
by escalating it, but it never rewrites the pipeline evidence or the raw
organiser submission.  Keeping this rule in core makes the web and exports
agree without teaching transport/storage code how decisions work.
"""
from __future__ import annotations

from typing import Any, Mapping

PROBLEM_PREFIX = "problem:"


def is_problem_feedback(feedback: "Mapping[str, Any] | None") -> bool:
    """Return whether a saved review says ClearDraft needs human attention."""
    if not feedback:
        return False
    verdict = feedback.get("verdict")
    note = feedback.get("note")
    return verdict == "flagged" or (
        verdict == "done"
        and isinstance(note, str)
        and note.strip().lower().startswith(PROBLEM_PREFIX)
        and bool(note.strip()[len(PROBLEM_PREFIX):].strip())
    )


def apply_feedback(
    status: str,
    review_reason: "str | None",
    defect_fields: "list[str]",
    has_defect: bool,
    feedback: "Mapping[str, Any] | None",
) -> dict:
    """Return the report-facing decision after a human-review overlay.

    A reported problem becomes NEEDS_REVIEW.  Existing defect fields and the
    original has_defect flag are deliberately preserved as source evidence.
    Confirmations are audit metadata only and do not alter the decision.
    """
    problem = is_problem_feedback(feedback)
    return {
        "status": "NEEDS_REVIEW" if problem else status,
        "review_reason": "human_feedback" if problem else review_reason,
        "defect_fields": list(defect_fields),
        "has_defect": bool(has_defect),
        "changed": problem and (status != "NEEDS_REVIEW" or review_reason != "human_feedback"),
    }
