from __future__ import annotations

from unittest.mock import patch

from core.classify import classify, classify_by_model, classify_by_rule
from core.types import Email


def email(subject: str, body: str, attachments: tuple[str, ...] = ()) -> Email:
    return Email(
        email_id="email_test",
        sender="ops@example.com",
        subject=subject,
        body=body,
        attachments=attachments,
    )


def test_rules_cover_all_five_categories() -> None:
    cases = (
        (
            email(
                "TO CONFIRM DOCS",
                "Attached are the SI and draft BL for OC 5RSG-00133. "
                "Please check the details and confirm.",
                ("x_SI.txt", "x_BL.txt"),
            ),
            "BL_COMPARISON",
        ),
        (
            email(
                "SI - MEDUUD104332 - DIRECT(MSC)",
                "Please find Shipping instruction for 5RSG-00133.",
            ),
            "SI_REQUEST",
        ),
        (
            email(
                "LOCAL CHARGES FOB",
                "Query on invoice 5250075931: is the THC included?",
            ),
            "INVOICE_QUERY",
        ),
        (
            email(
                "daily Berthing Report",
                "Please find attached the daily berthing report. Vessel berthed on schedule.",
            ),
            "GENERAL",
        ),
        (
            email(
                "URGENT: Your email storage is full",
                "Dear user, your mailbox has exceeded its storage limit. "
                "Verify your account now.",
            ),
            "SPAM",
        ),
    )

    for message, expected in cases:
        result = classify_by_rule(message)
        assert result is not None
        assert result.category == expected
        assert result.decided_by == "rule"
        assert result.confidence >= 0.9
        assert result.evidence


def test_general_si_and_bl_traps_remain_general() -> None:
    cases = (
        email(
            "_Reminder_Paper - Submit SI & AED_21-01-2026",
            "Reminder: Please submit SI & AED for all pending shipments by end of day.",
        ),
        email(
            "Pending BL Release 14_01_2026",
            "This is an automated notification. The India HSS SD Billing Process "
            "for MMSS 2507 V.257087E has completed successfully. No action required.",
        ),
        email(
            "APRIL PAPER - List of Outstanding BL (BDP SG) as of 2026-01-09",
            "Please find attached the list of outstanding BL (BDP SG). "
            "Kindly action the pending items.",
        ),
        email(
            "_RPA_ India HSS SD Billing Process Completed - MMSS 2507 V.257087E",
            "This is an automated notification. The India HSS SD Billing Process "
            "has completed successfully. No action required.",
        ),
    )

    for message in cases:
        result = classify_by_rule(message)
        assert result is not None
        assert result.category == "GENERAL"
        assert result.intent == "info"


def test_send_doc_and_compare_share_category_but_not_intent() -> None:
    send_doc = classify_by_rule(
        email(
            "Draft BL",
            "Please assist to send the draft BL for PSGSE9638346 for checking asap.",
        )
    )
    compare = classify_by_rule(
        email(
            "TO CONFIRM DOCS",
            "Please compare the SI and draft BL for 070500263211 and confirm "
            "(attachments appear to have been dropped).",
        )
    )

    assert send_doc is not None and compare is not None
    assert send_doc.category == compare.category == "BL_COMPARISON"
    assert send_doc.intent == "send_doc"
    assert compare.intent == "compare"


def test_quoted_history_does_not_override_current_request() -> None:
    result = classify_by_rule(
        email(
            "Delivery planning Jan 2026",
            "Please find attached the update summary for VISION 202.\n\n"
            "______________________________\nFrom: old@example.com\n"
            "Please compare the SI and draft BL and confirm.",
        )
    )

    assert result is not None
    assert result.category == "GENERAL"


def test_rule_declines_when_no_intent_phrase_matches() -> None:
    assert classify_by_rule(email("Hello", "Can you take a look?")) is None


def test_pipeline_runs_without_an_api_key() -> None:
    result = classify(email("Hello", "Can you take a look?"), use_model=True)

    assert result.category == "GENERAL"
    assert result.intent == "unknown"
    assert result.confidence == 0.0
    assert result.evidence == "no rule matched"


def test_valid_model_fallback_is_accepted() -> None:
    message = email("Question", "Could you review this operational update?")
    model_result = {
        "category": "GENERAL",
        "intent": "info",
        "confidence": 0.82,
        "evidence": "operational update",
    }

    with (
        patch("adapters.model.available", return_value=True),
        patch("adapters.model.complete_json", return_value=model_result),
    ):
        result = classify_by_model(message)

    assert result.category == "GENERAL"
    assert result.decided_by == "model"
    assert result.evidence == "operational update"


def test_model_evidence_must_appear_in_source() -> None:
    message = email("Question", "Could you review this operational update?")
    invented = {
        "category": "GENERAL",
        "intent": "info",
        "confidence": 0.82,
        "evidence": "invented phrase",
    }

    with (
        patch("adapters.model.available", return_value=True),
        patch("adapters.model.complete_json", return_value=invented),
    ):
        result = classify(message, use_model=True)

    assert result.category == "GENERAL"
    assert result.intent == "unknown"
    assert result.decided_by == "rule"
    assert result.confidence == 0.0


def test_model_category_and_intent_must_agree() -> None:
    message = email("Question", "Please review the invoice question.")
    incompatible = {
        "category": "INVOICE_QUERY",
        "intent": "compare",
        "confidence": 0.9,
        "evidence": "invoice question",
    }

    with (
        patch("adapters.model.available", return_value=True),
        patch("adapters.model.complete_json", return_value=incompatible),
    ):
        result = classify(message, use_model=True)

    assert result.intent == "unknown"
    assert result.evidence == "no rule matched"
