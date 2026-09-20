from __future__ import annotations

from unittest.mock import patch

import pytest

from adapters import model
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


@pytest.mark.parametrize(
    "body",
    (
        "Could you cross-check the draft BL with the shipping instructions?",
        "Kindly verify this bill of lading against the SI before release.",
        "Please confirm the draft BL matches our shipping instruction.",
        "Please confirm the draft matches the SI before we approve it.",
        "Can you validate the SI with the draft bill of lading?",
        "Review the attached shipping instructions and BL, then confirm.",
    ),
)
def test_comparison_rules_recognise_rephrased_meaning(body: str) -> None:
    result = classify_by_rule(email("Document review", body))

    assert result is not None
    assert result.category == "BL_COMPARISON"
    assert result.intent == "compare"


@pytest.mark.parametrize(
    "body",
    (
        "Would you share the draft bill of lading for our review?",
        "Please forward the BL to us for verification.",
        "Can you provide the draft BL so we can review it?",
    ),
)
def test_send_document_rules_recognise_rephrased_meaning(body: str) -> None:
    result = classify_by_rule(email("Draft document", body))

    assert result is not None
    assert result.category == "BL_COMPARISON"
    assert result.intent == "send_doc"


@pytest.mark.parametrize(
    ("body", "expected"),
    (
        ("Could you share the shipping instructions for booking 42?", "SI_REQUEST"),
        ("We need the SI for tomorrow's shipment.", "SI_REQUEST"),
        ("Can you explain this detention charge?", "INVOICE_QUERY"),
        ("Please correct invoice 8341 before payment.", "INVOICE_QUERY"),
    ),
)
def test_other_action_rules_recognise_rephrased_meaning(
    body: str, expected: str
) -> None:
    result = classify_by_rule(email("Operations question", body))

    assert result is not None
    assert result.category == expected


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


def test_unmatched_email_is_sorted_by_model_instead_of_general_default() -> None:
    message = email(
        "Accounts payable discrepancy",
        "Our payable total looks wrong. Could someone investigate?",
    )
    model_result = {
        "category": "INVOICE_QUERY",
        "intent": "invoice",
        "confidence": 0.91,
        "evidence": "payable total",
    }

    assert classify_by_rule(message) is None
    with (
        patch("adapters.model.available", return_value=True),
        patch("adapters.model.complete_json", return_value=model_result) as complete,
    ):
        result = classify(message)

    complete.assert_called_once()
    assert result.category == "INVOICE_QUERY"
    assert result.intent == "invoice"
    assert result.decided_by == "model"


@pytest.mark.skipif(
    not model.available(),
    reason="ANTHROPIC_API_KEY is not configured; live model test is opt-in",
)
def test_live_model_sorts_rule_residue() -> None:
    """Exercise the real adapter automatically once a model key is present."""
    message = email(
        "Accounts payable discrepancy",
        "Our payable total looks wrong. Could someone investigate?",
    )

    assert classify_by_rule(message) is None
    result = classify(message)

    assert result.category == "INVOICE_QUERY"
    assert result.intent == "invoice"
    assert result.decided_by == "model"


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
    message = email("Question", "Please review the payment concern.")
    incompatible = {
        "category": "INVOICE_QUERY",
        "intent": "compare",
        "confidence": 0.9,
        "evidence": "payment concern",
    }

    with (
        patch("adapters.model.available", return_value=True),
        patch("adapters.model.complete_json", return_value=incompatible),
    ):
        result = classify(message, use_model=True)

    assert result.intent == "unknown"
    assert result.evidence == "no rule matched"
