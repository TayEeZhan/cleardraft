from __future__ import annotations

import pytest

from core.units import describe_unit_difference, parse_weight


def test_parses_bare_number() -> None:
    w = parse_weight("21577")
    assert w is not None
    assert w.kg == 21577
    assert w.unit == ""


def test_parses_kg_and_kgs() -> None:
    assert parse_weight("21577 KG").kg == 21577
    assert parse_weight("21577 KGS").kg == 21577


def test_parses_mt() -> None:
    w = parse_weight("22 MT")
    assert w.kg == 22000
    assert w.unit == "MT"


def test_ton_variants_are_rejected_as_ambiguous() -> None:
    """"TON" is ambiguous: a US short ton is 907.18 kg, a UK long ton is
    1016.05 kg, and a metric tonne is 1000 kg - and in freight, a "revenue/
    measurement ton" is a different concept again (1 m^3 or 1000 kg,
    whichever is greater). Nothing printed on a shipping document
    distinguishes which one "TON" means, so TON/TONS/T all fail to parse and
    the row escalates rather than guessing. MT/MTS/TONNE/TONNES are
    unambiguous and keep converting at x1000.
    """
    assert parse_weight("22.5 TON") is None
    assert parse_weight("22.5 TONS") is None
    assert parse_weight("22.5 T") is None


def test_parses_lbs() -> None:
    w = parse_weight("1000 LBS")
    assert w.unit == "LBS"
    assert w.kg == round(1000 * 0.45359237)


def test_unit_without_space_is_parsed() -> None:
    assert parse_weight("22MT").kg == 22000


def test_unit_is_case_insensitive() -> None:
    assert parse_weight("22 mt").kg == 22000
    assert parse_weight("22 mt").unit == "MT"


def test_dotted_and_slashed_unit_tokens_are_canonicalised() -> None:
    assert parse_weight("22 M/T").unit == "MT"
    assert parse_weight("22 KGS.").unit == "KGS"


def test_garbage_input_returns_none() -> None:
    assert parse_weight("_______ MTS") is None
    assert parse_weight("") is None
    assert parse_weight(None) is None


def test_describe_unit_difference_reports_the_two_units() -> None:
    assert describe_unit_difference("22 MT", "22,000 KG") == "MT vs KG"


def test_describe_unit_difference_ignores_bare_vs_kg_spelling() -> None:
    # bare number is kilograms, so no real unit difference
    assert describe_unit_difference("21,577 KG", "21577") is None


def test_same_unit_spelled_differently_is_not_a_unit_difference() -> None:
    # "22 MT" and "22 TONNES" carry the SAME number in the SAME unit, so there is
    # nothing on the row for a person to check. Reporting a unit difference here
    # would put a note on an ordinary clean match.
    assert describe_unit_difference("22 MT", "22 TONNES") is None
    assert describe_unit_difference("22 MT", "22 MTS") is None
    assert describe_unit_difference("100 LBS", "100 POUNDS") is None


def test_describe_unit_difference_ignores_genuine_weight_mismatches() -> None:
    # different weights, not a unit-conversion case
    assert describe_unit_difference("22 MT", "22 KG") is None


def test_describe_unit_difference_ignores_unparseable_input() -> None:
    assert describe_unit_difference("22 QTL", "22 KG") is None


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "abc", "(.*[", "1" * 5000, 12345, 3.5],
)
def test_never_raises(value: object) -> None:
    parse_weight(value)
    describe_unit_difference(value, value)
