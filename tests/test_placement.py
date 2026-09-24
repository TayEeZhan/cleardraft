"""A model-read value must be in the right place, not merely on the page."""
from __future__ import annotations

from core.placement import locate_field_value


def test_correct_field_label_beats_wrong_and_unlabelled_occurrences() -> None:
    text = """Shipper: ACME PAPER LTD
ACME PAPER LTD
Consignee: ACME PAPER LTD
"""

    found = locate_field_value("consignee", "ACME PAPER LTD", text)

    assert found is not None
    assert found.line_no == 3
    assert found.label == "Consignee"
    assert found.score == 2


def test_unfamiliar_label_is_allowed_with_real_provenance() -> None:
    text = "Delivery Contact: ACME PAPER LTD\n"

    found = locate_field_value("consignee", "ACME PAPER LTD", text)

    assert found is not None
    assert found.line_no == 1
    assert found.raw == "Delivery Contact: ACME PAPER LTD"
    assert found.label == "Delivery Contact"
    assert found.label in found.raw
    assert found.score == 1


def test_value_found_only_under_a_different_field_is_rejected() -> None:
    text = "Shipper: ACME PAPER LTD\n"

    assert locate_field_value("consignee", "ACME PAPER LTD", text) is None


def test_value_spanning_a_line_break_is_rejected() -> None:
    text = "Consignee: ACME PAPER\nLTD\n"

    assert locate_field_value("consignee", "ACME PAPER LTD", text) is None


def test_unlabelled_occurrence_beats_a_contradicting_label() -> None:
    text = """  ACME PAPER LTD
Notify: ACME PAPER LTD
"""

    found = locate_field_value("consignee", "ACME PAPER LTD", text)

    assert found is not None
    assert found.line_no == 1
    assert found.score == 1
    # With no label, the source line itself is retained as honest provenance;
    # no synthetic "read by model" label is invented.
    assert found.label == "ACME PAPER LTD"

