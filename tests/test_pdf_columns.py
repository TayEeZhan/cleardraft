"""Two boxes on one physical line must not merge into one value.

OWNER: Sheng Kuan.

The organiser's PDFs are single-column, so nothing in the provided corpus
covers this. Real carrier drafts print Shipper and Consignee side by side and
a text extractor emits both on one line. Without a split the shipper value
swallows the consignee's label AND value - a value that is wrong rather than
missing, which compare() then trusts. These tests pin the behaviour before we
ever see a real draft.
"""
from __future__ import annotations

from core.parsers.pdf import _extract_fields


def _as_dict(line: str) -> dict[str, str]:
    return {field: value for field, _label, value in _extract_fields(line)}


# --------------------------------------------------------------------------
# The bug this fix exists for
# --------------------------------------------------------------------------
def test_two_boxes_without_colons_are_split() -> None:
    found = _as_dict("Shipper ABC PAPER LTD Consignee XYZ TRADING LLC")
    assert found["shipper"] == "ABC PAPER LTD"
    assert found["consignee"] == "XYZ TRADING LLC"


def test_two_boxes_with_colons_are_split() -> None:
    found = _as_dict("Shipper: ABC PAPER LTD Consignee: XYZ TRADING LLC")
    assert found["shipper"] == "ABC PAPER LTD"
    assert found["consignee"] == "XYZ TRADING LLC"


def test_three_boxes_on_one_line() -> None:
    found = _as_dict(
        "Port of Loading PORT KLANG Port of Discharge CALLAO "
        "Gross Weight 21,577 KG"
    )
    assert found["port_of_loading"] == "PORT KLANG"
    assert found["port_of_discharge"] == "CALLAO"
    assert found["gross_weight_kg"] == "21,577 KG"


def test_trailing_label_with_no_value_still_cuts() -> None:
    """The next box's label can be the last thing on the line."""
    found = _as_dict("Shipper ABC PAPER LTD Consignee")
    assert found["shipper"] == "ABC PAPER LTD"


# --------------------------------------------------------------------------
# The single-column case must be untouched: 28 PDFs depend on it
# --------------------------------------------------------------------------
def test_single_box_line_is_unchanged() -> None:
    found = _extract_fields("Shipper/Exporter APRIL FINE PAPER TRADING (M) SDN BHD")
    assert found == [
        ("shipper", "Shipper/Exporter", "APRIL FINE PAPER TRADING (M) SDN BHD")
    ]


def test_total_prefix_still_resolves() -> None:
    found = _as_dict("TOTAL Gross Wt (kgs): 131,322 KG")
    assert found["gross_weight_kg"] == "131,322 KG"


def test_line_with_no_known_label_yields_nothing() -> None:
    assert _extract_fields("RAKEZ AMENITY CENTER, AL HAMRA INDUSTRIAL ZONE") == []


# --------------------------------------------------------------------------
# A value must not be cut by something that only looks like a label
# --------------------------------------------------------------------------
def test_short_aliases_never_cut_a_value() -> None:
    """"pol"/"pod" are below the split threshold: too short to be safe."""
    found = _as_dict("Port of Discharge NHAVA SHEVA POD INDIA")
    assert found["port_of_discharge"] == "NHAVA SHEVA POD INDIA"


def test_repeat_of_the_same_field_does_not_cut() -> None:
    """Only a DIFFERENT field ends a value."""
    found = _as_dict("Gross Weight 21,577 KG gross weight")
    assert found["gross_weight_kg"] == "21,577 KG gross weight"


def test_label_inside_a_word_does_not_cut() -> None:
    found = _as_dict("Shipper CONSIGNEEHOLDINGS LTD")
    assert found["shipper"] == "CONSIGNEEHOLDINGS LTD"
