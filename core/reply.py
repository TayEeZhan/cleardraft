"""Draft the clerk's reply. Template fill only - never free model text.

OWNER: Ee Zhan.

The system NEVER sends. It drafts, a person reads, a person presses Send.
Every value in the draft is one we already verified against the source
document, so the reply cannot contain a number the documents do not contain.
"""
from __future__ import annotations

import re

from core.types import CompareField, Decision, Email

MISMATCH_TEMPLATE = """Hi {name},

We found {n} discrepanc{y} in the draft BL for {ref}:

{lines}

Please amend and resend the draft.
"""

CLEAR_TEMPLATE = """Hi {name},

No mismatch detected. Draft BL for {ref} is OK to proceed.
"""

REVIEW_TEMPLATE = """Hi {name},

We could not complete the check on {ref}: {reason}.
A colleague is reviewing this manually and will revert shortly.
"""

#: Human-readable rendering of each CompareField. Plain title-casing would
#: give "Port Of Loading" and "Gross Weight Kg", so this is spelled out.
FIELD_LABELS: dict[CompareField, str] = {
    "shipper": "Shipper",
    "consignee": "Consignee",
    "notify_party": "Notify Party",
    "port_of_loading": "Port of Loading",
    "port_of_discharge": "Port of Discharge",
    "container_count": "Container Count",
    "gross_weight_kg": "Gross Weight (kg)",
}

#: Readable phrase for each escalation reason, dropped into REVIEW_TEMPLATE.
REVIEW_REASON_PHRASES: dict[str, str] = {
    "missing_attachment": "the expected documents were not attached",
    "unreadable": "one of the documents could not be read",
    "wrong_doc_type": "one of the documents was not the expected SI/BL type",
    "missing_value": "a required field was missing or blank in the documents",
}

#: "Hi Najiha," / "Dear Hari," -> capture the first name.
_GREETING_RE = re.compile(r"^\s*(?:Hi|Dear|Hello)\s+([A-Za-z][A-Za-z'-]*)", re.IGNORECASE)

#: OC reference, e.g. "5RSG-00133": one digit, three letters, dash, five digits.
_OC_REF_RE = re.compile(r"\b\d[A-Z]{3}-\d{5}\b")

#: Fallback: a long alphanumeric booking/BL reference (letters and digits
#: mixed, no separators), e.g. "MEDUUD104332".
_BOOKING_REF_RE = re.compile(r"\b(?=[A-Z0-9]*[A-Z])(?=[A-Z0-9]*\d)[A-Z0-9]{8,}\b")


def _recipient_name(email: Email) -> str:
    try:
        body = email.body or ""
        m = _GREETING_RE.match(body)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        sender = email.sender or ""
        local = sender.split("@", 1)[0].strip()
        if local:
            return local
    except Exception:
        pass
    return "team"


def _reference(email: Email) -> str:
    try:
        subject = email.subject or ""
        m = _OC_REF_RE.search(subject)
        if m:
            return m.group(0)
        m = _BOOKING_REF_RE.search(subject)
        if m:
            return m.group(0)
    except Exception:
        pass
    return email.email_id or "this case"


def _field_line(comparison) -> str:
    label = FIELD_LABELS.get(comparison.field, str(comparison.field))
    si_val = comparison.si.value if comparison.si is not None else "(missing)"
    bl_val = comparison.bl.value if comparison.bl is not None else "(missing)"
    return f"- {label} — SI: {si_val} / BL: {bl_val}"


def _mismatch_lines(decision: Decision) -> str:
    by_field = {c.field: c for c in decision.comparisons}
    lines = []
    for fld in decision.defect_fields:
        c = by_field.get(fld)
        if c is None:
            label = FIELD_LABELS.get(fld, str(fld))
            lines.append(f"- {label} — SI: (unknown) / BL: (unknown)")
        else:
            lines.append(_field_line(c))
    if not lines:
        lines = ["- (no field detail available)"]
    return "\n".join(lines)


def _review_reason_phrase(decision: Decision) -> str:
    reason = decision.review_reason
    if reason and reason in REVIEW_REASON_PHRASES:
        return REVIEW_REASON_PHRASES[reason]
    return "the case needs manual review"


def draft_reply(email: Email, decision: Decision) -> str:
    """Pick a template by decision.status and fill it. Never raises."""
    try:
        return _draft_reply(email, decision)
    except Exception:
        try:
            return REVIEW_TEMPLATE.format(
                name="team",
                ref=getattr(email, "email_id", None) or "this case",
                reason="the case needs manual review",
            )
        except Exception:
            return (
                "Hi team,\n\nWe could not complete the check on this case.\n"
                "A colleague is reviewing this manually and will revert shortly.\n"
            )


def _draft_reply(email: Email, decision: Decision) -> str:
    name = _recipient_name(email)
    ref = _reference(email)

    if decision.status == "MISMATCH":
        n = len(decision.defect_fields)
        y = "y" if n == 1 else "ies"
        return MISMATCH_TEMPLATE.format(
            name=name, n=n, y=y, ref=ref, lines=_mismatch_lines(decision)
        )

    if decision.status == "NEEDS_REVIEW":
        return REVIEW_TEMPLATE.format(
            name=name, ref=ref, reason=_review_reason_phrase(decision)
        )

    # status == "OK". The spec-defined case is category BL_COMPARISON, but an
    # OK decision on any other category (GENERAL, SI_REQUEST, INVOICE_QUERY,
    # SPAM) also lands here and there is no fourth template to pick, so the
    # clear template - the closest to "nothing wrong here" - is reused.
    return CLEAR_TEMPLATE.format(name=name, ref=ref)
