"""Frozen contracts for the ClearDraft pipeline.

OWNER: Ee Zhan. Nobody else edits this file. If you need a change, ask.

Every stage of the pipeline is a pure function over these types. No stage
performs I/O except the adapters in `adapters/`. This is what makes the
pipeline testable, parallelisable and reproducible.

    Email -> Classification -> ExtractedDoc x2 -> FieldComparison x7 -> Decision
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

# --------------------------------------------------------------------------
# Enumerations. These strings are fixed by the organiser's scorer. Do not
# rename them. See docker/server/scoring.py.
# --------------------------------------------------------------------------
Category = Literal[
    "BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"
]
Status = Literal["OK", "MISMATCH", "NEEDS_REVIEW"]
ReviewReason = Literal[
    "wrong_doc_type", "missing_attachment", "unreadable", "missing_value",
    # Ours, not the organiser's: no rule matched and the model could not answer
    # (no key, timeout, or an answer that failed verification). The organiser
    # scorer counts only its own four reasons and ignores this one, so it costs
    # nothing there - and it keeps "we do not know" from becoming a silent OK.
    "unclassified",
]
DocKind = Literal["SI", "BL", "OTHER", "UNREADABLE"]
DecidedBy = Literal["rule", "model"]

#: The seven fields the organiser compares. Order is fixed so the UI table and
#: every report render in the same sequence.
CompareField = Literal[
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
]

COMPARE_FIELDS: tuple[CompareField, ...] = (
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
)

#: What the sender is asking for. Classification maps intent -> category, but
#: the intent itself is kept because the escalation gate needs it: a "send me
#: the draft BL" email with no attachments is OK, while a "compare these"
#: email with no attachments is missing_attachment. See PLAN.md section 1.6.
Intent = Literal[
    "compare",       # compare the attached SI against the attached BL
    "send_doc",      # please send / issue / share a document
    "request_si",    # please provide the shipping instruction
    "invoice",       # billing, charges, credit note, invoice cancellation
    "info",          # status updates, reports, reminders, internal notices
    "spam",
    "unknown",
]


# --------------------------------------------------------------------------
# Stage 0 - input
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Email:
    """One inbox record. Mirrors inbox/email_NNN.json.

    `sender` holds the JSON's "from" key, which is a Python keyword.
    """

    email_id: str
    sender: str
    subject: str
    body: str
    attachments: tuple[str, ...] = ()

    @classmethod
    def from_json(cls, raw: Mapping[str, object]) -> "Email":
        return cls(
            email_id=str(raw["email_id"]),
            sender=str(raw.get("from", "")),
            subject=str(raw.get("subject", "")),
            body=str(raw.get("body", "")),
            attachments=tuple(str(a) for a in (raw.get("attachments") or ())),
        )


# --------------------------------------------------------------------------
# Stage 1 - classification   (OWNER: Zi Qi)
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Classification:
    category: Category
    intent: Intent
    decided_by: DecidedBy
    #: 0.0 - 1.0. Rule hits should be >= 0.9. Anything below the router's
    #: threshold is handed to the model.
    confidence: float
    #: The exact phrase that triggered the decision. Shown in the UI and used
    #: when debugging a misclassification. Never leave this empty.
    evidence: str = ""


# --------------------------------------------------------------------------
# Stage 2 - extraction   (OWNER: Sheng Kuan)
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FieldValue:
    """One field read out of one document, with its proof.

    `raw` and `line_no` are not optional extras. They power two things:
      1. the "show me where you read that" panel in the review screen, and
      2. the verbatim-verification gate that rejects invented model output.
    """

    value: str          # the extracted text, trimmed. Not yet normalised.
    raw: str            # the full source line the value came from
    line_no: int        # 1-indexed. 0 means "position unknown" (xlsx cells).
    label: str          # the document's own label, e.g. "Gross Wt (kgs)"
    decided_by: DecidedBy = "rule"


@dataclass(frozen=True, slots=True)
class ExtractedDoc:
    """The result of reading one attachment."""

    path: str
    kind: DocKind
    #: Only fields that were found. A missing key means "not present in this
    #: document", which is different from "present but blank" - a blank value
    #: is stored with value="" so the missing_value gate can see it.
    fields: Mapping[CompareField, FieldValue] = field(default_factory=dict)
    #: Full plain text of the document. The verification gate searches this.
    text: str = ""
    readable: bool = True
    #: Populated when readable is False. Shown to the operator, never hidden.
    error: str | None = None


# --------------------------------------------------------------------------
# Stage 3 - comparison   (OWNER: Ee Zhan)
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FieldComparison:
    """One row of the seven-row review table."""

    field: CompareField
    si: FieldValue | None
    bl: FieldValue | None
    #: Normalised forms. int for container_count and gross_weight_kg, str for
    #: the rest, None when the field is absent or blank.
    si_norm: str | int | None
    bl_norm: str | int | None
    matched: bool
    #: True when this row could not be decided (either side missing or blank).
    #: An undecidable row escalates the whole email rather than counting as a
    #: mismatch. Guessing here is how you lose end-to-end points.
    undecidable: bool = False


# --------------------------------------------------------------------------
# Stage 4 - decision
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Decision:
    email_id: str
    category: Category
    status: Status
    review_reason: ReviewReason | None
    has_defect: bool
    defect_fields: tuple[CompareField, ...]
    decided_by: DecidedBy
    comparisons: tuple[FieldComparison, ...] = ()
    reply_draft: str | None = None
    #: Free-text explanation for the operator. Not scored, but it is what
    #: makes the tool trustworthy rather than a black box.
    rationale: str = ""

    def to_submission(self) -> dict[str, object]:
        """Render the exact record shape the organiser's scorer expects.

        `defect_fields` is sorted because the scorer compares sets, and a
        stable order makes diffs between runs readable.
        """
        return {
            "category": self.category,
            "status": self.status,
            "review_reason": self.review_reason,
            "has_defect": self.has_defect,
            "defect_fields": sorted(self.defect_fields),
            # Optional. The scorer reads it and reports rule_pct. Free evidence
            # that our decisions are mostly deterministic. See PLAN.md 1.7.
            "decided_by": self.decided_by,
        }


__all__ = [
    "Category", "Status", "ReviewReason", "DocKind", "DecidedBy",
    "CompareField", "COMPARE_FIELDS", "Intent",
    "Email", "Classification", "FieldValue", "ExtractedDoc",
    "FieldComparison", "Decision",
]
