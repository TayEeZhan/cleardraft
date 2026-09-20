"""Stage 1: decide what the email is asking for.

OWNER: Zi Qi.

TARGET: macro-F1 >= 0.95 on your dev slice. This criterion is 30% of the
organiser's score.

TWO TRAPS THAT WILL COST YOU POINTS
-----------------------------------
1. KEYWORDS DO NOT WORK. These subjects are all GENERAL:
     "_Reminder_Paper - Submit SI & AED_21-01-2026"
     "Pending BL Release 14_01_2026"
     "APRIL PAPER - List of Outstanding BL (BDP SG) as of 2026-01-09"
   and SI_REQUEST subjects routinely contain a BL number. Match the INTENT
   VERB (compare / send / provide / query / notify), not the nouns SI and BL.

2. INTENT IS NOT THE SAME AS CATEGORY, and the difference matters downstream.
     "Please assist to send the draft BL for X for checking"
         -> category BL_COMPARISON, intent "send_doc"
     "Please compare the SI and draft BL for X and confirm"
         -> category BL_COMPARISON, intent "compare"
   Both are BL_COMPARISON. Only the second one escalates when the attachments
   are absent. 94 of the 220 comparison emails have zero attachments and 91 of
   those are ground-truth OK. Returning intent="compare" for all of them
   destroys our escalation precision. See PLAN.md section 1.6.

The `evidence` field must hold the phrase that decided it. The review UI shows
it, and it is how you debug a confusion-matrix cell at 2am.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from core.types import Classification, Email

#: Below this, hand the email to the model instead of trusting the rule.
CONFIDENCE_FLOOR = 0.60


Rule = tuple[re.Pattern[str], str, str, float]


def _rules(*items: tuple[str, str, str, float]) -> tuple[Rule, ...]:
    return tuple(
        (re.compile(pattern, re.IGNORECASE | re.DOTALL), category, intent, confidence)
        for pattern, category, intent, confidence in items
    )


# Rules are ordered from the most explicit ask to broader operational forms.
# Each pattern is an intent phrase, not a bare domain noun such as "SI" or
# "BL". This is what keeps reminders and outstanding-document lists out of
# the action categories.
_SPAM_RULES = _rules(
    (r"mailbox has exceeded its storage limit", "SPAM", "spam", 0.99),
    (r"congratulations!{0,3}.*(?:selected|won|gift card)", "SPAM", "spam", 0.99),
    (r"limited time offer!?.*\b(?:90% off|buy now)\b", "SPAM", "spam", 0.99),
    (r"package could not be delivered.*unpaid customs fee", "SPAM", "spam", 0.99),
    (r"won a brand new iphone.*\bclaim\b", "SPAM", "spam", 0.99),
    (r"guaranteed 300% returns", "SPAM", "spam", 0.99),
    (r"hot singles in your area", "SPAM", "spam", 0.99),
    (r"undelivered messages in your mailbox", "SPAM", "spam", 0.99),
    (r"email storage is full.*verify account", "SPAM", "spam", 0.99),
    (r"update your account to avoid suspension", "SPAM", "spam", 0.99),
    (r"bank officer with an urgent business proposal", "SPAM", "spam", 0.99),
)

_SI_MENTION = re.compile(r"\b(?:si|shipping\s+instructions?)\b", re.IGNORECASE)
_BL_MENTION = re.compile(
    r"\b(?:draft(?:\s+(?:bl|bill\s+of\s+lading))?|bl|bill\s+of\s+lading)\b",
    re.IGNORECASE,
)
_SEND_BL_FOR_REVIEW = re.compile(
    r"\b(?:send|share|issue|provide|forward|release)\b"
    r".{0,100}?\b(?:draft\s+)?(?:bl|bill\s+of\s+lading)\b"
    r"(?:.{0,120}?\b(?:for|to)\s+(?:our\s+)?"
    r"(?:check(?:ing)?|review|verification)\b"
    r"|.{0,120}?\bso\s+(?:we|i)\s+can\s+(?:check|review|verify)\b)",
    re.IGNORECASE | re.DOTALL,
)
_RELATIONAL_COMPARE = re.compile(
    r"\b(?:cross[\s-]?check|compare|reconcile)\b"
    r"|\b(?:check|verify|validate)\b.{0,100}?\b(?:against|with)\b"
    r"|\b(?:confirm|ensure|verify)\b.{0,140}?"
    r"\b(?:match(?:es|ed)?|align(?:s|ed)?|agree(?:s|d)?|correspond(?:s|ed)?)\b",
    re.IGNORECASE | re.DOTALL,
)
_DOCUMENT_REVIEW = re.compile(
    r"\b(?:check(?:ing)?|verify|verification|confirm|validate|review)\b",
    re.IGNORECASE,
)

_SI_REQUEST_RULES = _rules(
    (
        r"please find shipping instruction for\s+[a-z0-9-]+",
        "SI_REQUEST",
        "request_si",
        0.98,
    ),
    (
        r"\b(?:provide|issue|send|share|prepare|forward)\s+"
        r"(?:(?:me|us)\s+)?(?:the\s+)?(?:shipping\s+instructions?|si)\b",
        "SI_REQUEST",
        "request_si",
        0.96,
    ),
    (
        r"\b(?:need|require|request)\b[^\r\n]{0,60}?"
        r"\b(?:shipping\s+instructions?|si)\b",
        "SI_REQUEST",
        "request_si",
        0.95,
    ),
)

_INVOICE_RULES = _rules(
    (r"query on invoice\s+\d+", "INVOICE_QUERY", "invoice", 0.99),
    (r"d\s*&\s*d\s*/\s*detention charges\b", "INVOICE_QUERY", "invoice", 0.99),
    (r"gr is still missing for invoice\s+\d+", "INVOICE_QUERY", "invoice", 0.99),
    (r"requesting to cancel invoice\s+\d+", "INVOICE_QUERY", "invoice", 0.99),
    (r"(?:credit|debit) note\b", "INVOICE_QUERY", "invoice", 0.95),
    (
        r"\b(?:question|query|clarify|explain|dispute)\b.{0,100}?"
        r"\b(?:invoice|billing|charges?|fees?|surcharges?)\b",
        "INVOICE_QUERY",
        "invoice",
        0.96,
    ),
    (
        r"\b(?:cancel|correct|amend|reverse)\b.{0,80}?\binvoice\b",
        "INVOICE_QUERY",
        "invoice",
        0.96,
    ),
)

_GENERAL_RULES = _rules(
    (r"daily berthing report\b", "GENERAL", "info", 0.98),
    (r"please find attached the update summary\b", "GENERAL", "info", 0.98),
    (r"list of outstanding bl\b", "GENERAL", "info", 0.98),
    (
        r"india hss sd billing process\b.{0,160}?completed successfully",
        "GENERAL",
        "info",
        0.98,
    ),
    (r"reminder:\s*please submit si\s*&\s*aed\b", "GENERAL", "info", 0.98),
    (r"wishing everyone a happy and prosperous new year", "GENERAL", "info", 0.98),
    (r"time off request", "GENERAL", "info", 0.95),
)

_RULE_GROUPS: tuple[tuple[Rule, ...], ...] = (
    _SPAM_RULES,
    _SI_REQUEST_RULES,
    _INVOICE_RULES,
    _GENERAL_RULES,
)

_INTENTS_BY_CATEGORY: dict[str, frozenset[str]] = {
    "BL_COMPARISON": frozenset(("compare", "send_doc")),
    "SI_REQUEST": frozenset(("request_si",)),
    "INVOICE_QUERY": frozenset(("invoice",)),
    "GENERAL": frozenset(("info",)),
    "SPAM": frozenset(("spam",)),
}


def _current_message(email: Email) -> str:
    """Return subject plus the current message, excluding quoted history."""
    body = email.body
    for marker in (
        "______________________________\nFrom:",
        "-----Original Message-----",
    ):
        if marker in body:
            body = body.split(marker, 1)[0]
    return f"{email.subject}\n{body}"


def _match_rules(text: str, groups: Sequence[Sequence[Rule]]) -> Classification | None:
    for group in groups:
        for pattern, category, intent, confidence in group:
            match = pattern.search(text)
            if match is not None:
                evidence = " ".join(match.group(0).split())
                return Classification(
                    category=category,  # type: ignore[arg-type]
                    intent=intent,  # type: ignore[arg-type]
                    decided_by="rule",
                    confidence=confidence,
                    evidence=evidence,
                )
    return None


def _comparison_rule(text: str) -> Classification | None:
    """Recognise the comparison meaning without requiring a fixed sentence."""
    send_match = _SEND_BL_FOR_REVIEW.search(text)
    if send_match is not None:
        return Classification(
            category="BL_COMPARISON",
            intent="send_doc",
            decided_by="rule",
            confidence=0.98,
            evidence=" ".join(send_match.group(0).split()),
        )

    # A comparison needs both document concepts. This guard stops phrases such
    # as "verify the invoice" or "outstanding BL" becoming false positives.
    if _SI_MENTION.search(text) is None or _BL_MENTION.search(text) is None:
        return None

    action_match = _RELATIONAL_COMPARE.search(text) or _DOCUMENT_REVIEW.search(text)
    if action_match is None:
        return None
    return Classification(
        category="BL_COMPARISON",
        intent="compare",
        decided_by="rule",
        confidence=0.97,
        evidence=" ".join(action_match.group(0).split()),
    )


def classify(email: Email, *, use_model: bool = True) -> Classification:
    """Rules first, model only on the residue. Never raises."""
    hit = classify_by_rule(email)
    if hit is not None and hit.confidence >= CONFIDENCE_FLOOR:
        return hit

    if use_model:
        try:
            return classify_by_model(email)
        except Exception:
            # ModelUnavailable, a transient provider failure, malformed JSON,
            # or an unfinished adapter must not terminate the 520-email batch.
            pass

    return hit or Classification(
        category="GENERAL",
        intent="unknown",
        decided_by="rule",
        confidence=0.0,
        evidence="no rule matched",
    )


def classify_by_rule(email: Email) -> Classification | None:
    """High-precision rules. Return None rather than guessing.

    Precision matters more than recall here: an unsure rule should decline so
    the model can decide, because a wrong rule is silent and a declined rule
    is measurable.
    """
    text = _current_message(email)
    spam = _match_rules(text, (_SPAM_RULES,))
    if spam is not None:
        return spam
    comparison = _comparison_rule(text)
    if comparison is not None:
        return comparison
    return _match_rules(text, _RULE_GROUPS[1:])


def classify_by_model(email: Email) -> Classification:
    """Classify rule residue through the project's single model adapter."""
    from adapters.model import ModelUnavailable, available, complete_json

    if not available():
        raise ModelUnavailable("ANTHROPIC_API_KEY is not configured")

    prompt = (
        "Classify the current shipping-operations email by the requested action. "
        "Ignore quoted history, signatures, warning banners, and nouns that do not "
        "express the current ask. Return JSON only.\n\n"
        "Categories and compatible intents:\n"
        "BL_COMPARISON: compare | send_doc\n"
        "SI_REQUEST: request_si\n"
        "INVOICE_QUERY: invoice\n"
        "GENERAL: info\n"
        "SPAM: spam\n\n"
        "Evidence must be a short exact phrase copied from the subject or body.\n"
        f"EMAIL:\n{json.dumps({'subject': email.subject, 'body': email.body, 'attachment_count': len(email.attachments)})}"
    )
    raw = complete_json(
        prompt,
        schema_hint=(
            '{"category":"BL_COMPARISON|SI_REQUEST|INVOICE_QUERY|GENERAL|SPAM",'
            '"intent":"compare|send_doc|request_si|invoice|info|spam",'
            '"confidence":0.0,"evidence":"exact phrase from email"}'
        ),
        max_tokens=160,
    )

    category = raw.get("category")
    intent = raw.get("intent")
    evidence = raw.get("evidence")
    confidence = raw.get("confidence")
    if category not in _INTENTS_BY_CATEGORY:
        raise ValueError("model returned an unknown category")
    if intent not in _INTENTS_BY_CATEGORY[category]:
        raise ValueError("model returned an incompatible intent")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("model returned empty evidence")
    if evidence.casefold() not in _current_message(email).casefold():
        raise ValueError("model evidence does not appear in the email")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError("model returned invalid confidence")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("model confidence is outside 0..1")

    return Classification(
        category=category,  # type: ignore[arg-type]
        intent=intent,  # type: ignore[arg-type]
        decided_by="model",
        confidence=confidence,
        evidence=evidence.strip(),
    )
