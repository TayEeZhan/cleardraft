from __future__ import annotations

import pytest

from core.normalise import (
    normalise,
    normalise_container_count,
    normalise_entity,
    normalise_port,
    normalise_weight,
)


def test_port_locode_is_formatting_not_content() -> None:
    assert normalise_port("CALLAO, PERU (PECLL)") == normalise_port("CALLAO, PERU")


def test_container_count_discards_size_but_not_quantity() -> None:
    assert normalise_container_count("6 x 40'HC") == 6
    assert normalise_container_count("1 x 20GP") == 1
    assert normalise_container_count("3 x 40'HC") != normalise_container_count(
        "4 x 40'HC"
    )


def test_container_count_plain_values() -> None:
    assert normalise_container_count("6") == 6
    assert normalise_container_count("6 CONTAINERS") == 6
    assert normalise_container_count("") is None
    assert normalise_container_count(None) is None


#: The full decision table for the fail-closed rule: a wrong MATCH is the
#: expensive error here (core/compare.py turns a None on either side into
#: "undecidable", never a match - see core/decide.py's precedence ladder),
#: so anything genuinely ambiguous about the quantity returns None rather
#: than a guess, and only a single unambiguous "N x <size>" group - or a
#: plain value with no "x" at all - returns an int.
_CONTAINER_COUNT_CASES = [
    # Mixed equipment: more than one "N x <size>" group. NOT summed - see
    # normalise_container_count's docstring for why summing would be wrong.
    ("2 x 40HC + 1 x 20GP", None),
    ("2 x 40HC + 3 x 20GP", None),
    ("1 X 20GP, 2 X 40HC", None),
    ("5 CONTAINERS: 4 X 40HC + 1 X 20GP", None),
    # Plain values: no "x" pattern, old leading-integer rule applies.
    ("6 x 40'HC", 6),
    ("6", 6),
    ("6 CONTAINERS", 6),
    # Exactly one "N x <size>" group: unambiguous.
    ("2x40HC", 2),
    ("1X40HC", 1),
    ("10 X 40HC", 10),
    ("SAID TO CONTAIN 2 X 40HC", 2),
    ("2 X 20' = 40 TEU", 2),
    ("1 x 40HC WITH 2 X SEALS", 1),
    # Trailing "*" is noise, not a second multiplier, so this has zero "N x
    # <size>" terms and falls back to the leading integer.
    ("2 X 40 *", 2),
    # Not a digit at the front, and the "2" is never recognised as an "N x"
    # quantity (it is inside parentheses) - unparseable either way.
    ("TWO (2) X 40HC", None),
    # Size BEFORE the multiplier: which number is the quantity is genuinely
    # ambiguous, so this always fails closed, never guessed at.
    ("40' x 2", None),
    ("40HC X 3", None),
    ("20GP x 3", None),
    ("20' x 2 + 40' x 1", None),
    # Blank/unparseable.
    ("", None),
    (None, None),
]


@pytest.mark.parametrize("value, expected", _CONTAINER_COUNT_CASES)
def test_container_count_table(value: "str | None", expected: "int | None") -> None:
    assert normalise_container_count(value) == expected


def test_weight_accepts_formatted_and_raw_xlsx_values() -> None:
    assert normalise_weight("21,577 KG") == 21577
    assert normalise_weight("21577") == 21577
    assert normalise_weight(21577) == 21577


def test_entity_punctuation_is_formatting_not_content() -> None:
    assert normalise_entity("MOORIM SP CO., LTD") == normalise_entity(
        "MOORIM SP CO LTD"
    )


def test_distinct_entities_do_not_collapse() -> None:
    assert normalise_entity("MOORIM SP CO., LTD") != normalise_entity("UAB NOVAKOPA")


def test_entity_continuation_marker_is_formatting_not_content() -> None:
    # Workshop finding: a trailing/wrapping "*" or "**" marks a party name
    # that continues elsewhere on the page - not a discrepancy.
    assert normalise_entity("EAST BRIGHT FZ-LLC *") == normalise_entity(
        "EAST BRIGHT FZ-LLC"
    )
    assert normalise_entity("**UAB NOVAKOPA**") == normalise_entity("UAB NOVAKOPA")
    assert normalise_entity("MOORIM SP CO., LTD *") == normalise_entity(
        "MOORIM SP CO LTD"
    )


def test_entity_continuation_marker_does_not_mask_real_defects() -> None:
    assert normalise_entity("UAB NOVAKOPA *") != normalise_entity("EAST BRIGHT FZ-LLC")


def test_entity_extra_spacing_is_formatting_not_content() -> None:
    assert normalise_entity("KTP  CO.,   LTD") == normalise_entity("KTP CO., LTD")


def test_blank_required_values_are_undecidable() -> None:
    for token in ("", "???", "_______", "TBA", "TBC", "N/A"):
        assert normalise("shipper", token) is None
        assert normalise("gross_weight_kg", token) is None


def test_weight_units_are_converted_not_discarded() -> None:
    assert normalise_weight("22 MT") == 22000
    assert normalise_weight("48,500 LBS") == 21999
    assert normalise_weight("21,577 KG") == 21577
    assert normalise_weight("21577") == 21577


def test_weight_unit_mismatch_is_a_defect() -> None:
    assert normalise_weight("22 MT") != normalise_weight("22 KG")


def test_same_weight_in_different_units_matches() -> None:
    assert normalise_weight("22 MT") == normalise_weight("22,000 KG")


def test_unsupported_weight_unit_escalates() -> None:
    assert normalise_weight("22 QTL") is None
    assert normalise_weight("22 TONS") is None
    assert normalise_weight("22 TON") is None


def test_weight_decimal_survives_conversion() -> None:
    assert normalise_weight("22.5 MT") == 22500
