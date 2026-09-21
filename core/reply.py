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

NOTHING_TO_CHECK_TEMPLATE = """Hi {name},

Noted on {ref}. No document comparison was required for this message, so no
SI/BL check was carried out.
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


#: Start of a forwarded/quoted tail. Anything past this belongs to an earlier
#: message, so a reference found there may name a different shipment.
_QUOTED_TAIL_RE = re.compile(r"\n\s*(?:_{5,}|-{5,}|From:\s)", re.MULTILINE)


def _own_body(email: Email) -> str:
    """The part of the body this sender actually wrote."""
    body = email.body or ""
    m = _QUOTED_TAIL_RE.search(body)
    return body[: m.start()] if m else body


def _reference(email: Email) -> str:
    """Prefer the coded OC reference. Subject first, then the sender's own text.

    55 of the 520 emails put the OC reference only in the body, e.g. subject
    "REQUEST BL DRAFT _ PO 26067_ COATED IVORY BOARD__138MT" with the real
    reference 5ALT-01226 in the first line. Falling straight through to
    email_id would draft "draft BL for email_004", which means nothing to a
    clerk and makes the reply useless.
    """
    try:
        subject = email.subject or ""
        for pattern in (_OC_REF_RE, _BOOKING_REF_RE):
            m = pattern.search(subject)
            if m:
                return m.group(0)
        own = _own_body(email)
        for pattern in (_OC_REF_RE, _BOOKING_REF_RE):
            m = pattern.search(own)
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
    except Exception as exc:
        # Escalate in wording AND record what happened. A drafting bug that
        # silently degrades every MISMATCH into a generic "needs review" note
        # would be indistinguishable from ordinary escalation, so the reason
        # is carried in the text the operator actually reads.
        detail = f"reply drafting failed ({exc.__class__.__name__}: {exc})"
        try:
            return REVIEW_TEMPLATE.format(
                name="team",
                ref=getattr(email, "email_id", None) or "this case",
                reason=detail,
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

    # status == "OK". Only claim a clean check when a check actually ran.
    # Saying "No mismatch detected. Draft BL is OK to proceed" on an email
    # where no documents were compared - an invoice query, or a request to
    # SEND a draft BL - tells the clerk a verification happened when none did.
    if decision.category == "BL_COMPARISON" and decision.comparisons:
        return CLEAR_TEMPLATE.format(name=name, ref=ref)

    return NOTHING_TO_CHECK_TEMPLATE.format(name=name, ref=ref)


# ---------------------------------------------------------------------------
# Follow-up after the carrier sends an amended draft. Same rule as above:
# template fill only, built from values compare() already verified.
# ---------------------------------------------------------------------------
RECHECK_CLEAR_TEMPLATE = """Hi {name},

Thanks for the amended draft BL for {ref}. All 7 fields now match the SI.
OK to proceed.
"""

RECHECK_TEMPLATE = """Hi {name},

Thanks for the amended draft BL for {ref}. We re-checked all 7 fields.

{sections}

Please amend and resend.
"""


def _v2_line(row) -> str:
    c = row.v2
    label = FIELD_LABELS.get(row.field, str(row.field))
    si_val = c.si.value if c.si is not None else "(missing)"
    bl_val = c.bl.value if c.bl is not None else "(missing)"
    return f"- {label} - SI: {si_val} / BL: {bl_val}"


def draft_recheck_reply(email: Email, rows) -> str:
    """Never raises. rows come from core.recheck.recheck()."""
    try:
        name, ref = _recipient_name(email), _reference(email)
        still = [r for r in rows if r.outcome == "still_wrong"]
        broke = [r for r in rows if r.outcome == "newly_broken"]
        unread = [r for r in rows if r.outcome == "unreadable"]
        fixed = [r for r in rows if r.outcome == "fixed"]
        if not (still or broke or unread):
            return RECHECK_CLEAR_TEMPLATE.format(name=name, ref=ref)
        parts = []
        if fixed:
            parts.append("Now correct: " + ", ".join(FIELD_LABELS.get(r.field, r.field) for r in fixed) + ".")
        if still:
            parts.append("Still not matching the SI:\n" + "\n".join(_v2_line(r) for r in still))
        if broke:
            parts.append("Changed in this amendment and now wrong:\n" + "\n".join(_v2_line(r) for r in broke))
        if unread:
            parts.append("Could not be read in the amended draft: "
                         + ", ".join(FIELD_LABELS.get(r.field, r.field) for r in unread) + ".")
        return RECHECK_TEMPLATE.format(name=name, ref=ref, sections="\n\n".join(parts))
    except Exception as exc:
        return REVIEW_TEMPLATE.format(
            name="team", ref=getattr(email, "email_id", "this case"),
            reason=f"re-check reply drafting failed ({exc.__class__.__name__}: {exc})",
        )
