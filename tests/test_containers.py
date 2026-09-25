"""Pure tests for core/containers.py: Composition parsing and the
container_verdict gate that core/compare.py applies to container_count.

No I/O anywhere in this file.
"""
from __future__ import annotations

import pytest

from core.containers import (
    Composition,
    container_verdict,
    parse_composition,
    sizes_present_in,
    total_for,
)

# ---------------------------------------------------------------------------
# parse_composition / total_for
# ---------------------------------------------------------------------------
def test_single_group_captures_size_and_count():
    comp = parse_composition("6 x 40'HC")
    assert comp == Composition(total=6, groups=(("40HC", 6),))


def test_multi_group_now_sums_the_total():
    """core/normalise.py's old rule refused to sum mixed equipment at all
    (commit 15c49b1). That refusal is gone: the total is used to answer
    "how many boxes", and container_verdict (not the total) is what stops
    two differently-split shipments from silently matching."""
    comp = parse_composition("2 x 40HC + 1 x 20GP")
    assert comp.total == 3
    assert comp.groups == (("20GP", 1), ("40HC", 2))


def test_multi_group_canonical_key_is_sorted_and_uppercase():
    comp = parse_composition("1 X 20gp, 2 X 40hc")
    assert comp.groups == (("20GP", 1), ("40HC", 2))


def test_size_before_multiplier_is_unparseable():
    assert parse_composition("40' x 2") is None
    assert parse_composition("40HC X 3") is None
    assert parse_composition("20GP x 3") is None
    assert parse_composition("20' x 2 + 40' x 1") is None


def test_plain_leading_integer_has_no_groups():
    comp = parse_composition("6")
    assert comp == Composition(total=6, groups=None)
    comp = parse_composition("6 CONTAINERS")
    assert comp == Composition(total=6, groups=None)


def test_trailing_star_is_noise_not_a_second_group():
    comp = parse_composition("2 X 40 *")
    assert comp == Composition(total=2, groups=None)


def test_blank_or_unparseable_is_none():
    assert parse_composition("") is None
    assert parse_composition(None) is None
    assert total_for("") is None
    assert total_for(None) is None


def test_total_for_mirrors_composition_total():
    assert total_for("6 x 40'HC") == 6
    assert total_for("2 x 40HC + 1 x 20GP") == 3
    assert total_for("6") == 6
    assert total_for("40' x 2") is None


# ---------------------------------------------------------------------------
# sizes_present_in
# ---------------------------------------------------------------------------
def test_sizes_present_in_detects_common_forms():
    assert sizes_present_in("SHIPPED IN 1 X 40'HC CONTAINER") is True
    assert sizes_present_in("EQUIPMENT: 40FT HC") is True
    assert sizes_present_in("20GP UNIT") is True
    assert sizes_present_in("ONE 45HQ CONTAINER") is True


def test_sizes_present_in_does_not_match_weights():
    """A weight number must never be mistaken for a container size."""
    assert sizes_present_in("GROSS WEIGHT 48,400 KG") is False
    assert sizes_present_in("NET WEIGHT 22,040 KG") is False
    assert sizes_present_in("TOTAL 20400 KG") is False


def test_sizes_present_in_false_when_nothing_mentioned():
    assert sizes_present_in("SIX CONTAINERS SAID TO CONTAIN GENERAL CARGO") is False
    assert sizes_present_in("") is False
    assert sizes_present_in(None) is False


# ---------------------------------------------------------------------------
# container_verdict: the exact decision table from the spec
# ---------------------------------------------------------------------------
def test_identical_mixed_composition_agrees():
    si = bl = "2 x 40HC + 1 x 20GP"
    assert container_verdict(si, bl, si, bl) == "agree"


def test_same_total_different_size_differs():
    assert container_verdict("6 x 40'HC", "6 x 20GP", "", "") == "differ"


def test_same_total_different_split_differs():
    si = "2 x 40HC + 1 x 20GP"
    bl = "1 x 40HC + 2 x 20GP"
    assert container_verdict(si, bl, si, bl) == "differ"


def test_different_totals_differ():
    assert container_verdict("6 x 40'HC", "5 x 40'HC", "", "") == "differ"


def test_bare_count_agrees_when_other_side_states_size_and_document_confirms_it():
    bl_text = "SAID TO CONTAIN 6 X 40'HC. NO MARKS."
    assert container_verdict("6 x 40'HC", "6", "", bl_text) == "agree"


def test_bare_count_unverifiable_when_no_size_stated_anywhere():
    bl_text = "SIX CONTAINERS, SAID TO CONTAIN GENERAL CARGO."
    assert container_verdict("6 x 40'HC", "6", "", bl_text) == "unverifiable"


def test_both_bare_counts_agree_when_totals_match():
    assert container_verdict("6", "6", "", "") == "agree"


def test_size_before_multiplier_is_unverifiable():
    assert container_verdict("40HC x 2", "2", "", "") == "unverifiable"


def test_ambiguous_spelled_out_multiplier_is_unverifiable():
    assert container_verdict("TWO x 40HC", "2", "", "") == "unverifiable"


def test_unparseable_or_blank_is_unverifiable():
    assert container_verdict("6", "???", "", "") == "unverifiable"
    assert container_verdict("6", "", "", "") == "unverifiable"
    assert container_verdict("6", None, "", "") == "unverifiable"


@pytest.mark.parametrize(
    "si_value, bl_value, si_text, bl_text, expected",
    [
        ("2 x 40HC + 1 x 20GP", "2 x 40HC + 1 x 20GP", "", "", "agree"),
        ("6 x 40'HC", "6 x 40'HC", "", "", "agree"),
        ("6 x 40'HC", "6 x 20GP", "", "", "differ"),
        (
            "2 x 40HC + 1 x 20GP",
            "1 x 40HC + 2 x 20GP",
            "",
            "",
            "differ",
        ),
        ("6 x 40'HC", "5 x 40'HC", "", "", "differ"),
        ("6 x 40'HC", "6", "", "SAID TO CONTAIN 40'HC.", "agree"),
        ("6 x 40'HC", "6", "", "GENERAL CARGO.", "unverifiable"),
        ("6", "6", "", "", "agree"),
    ],
)
def test_container_verdict_decision_table(si_value, bl_value, si_text, bl_text, expected):
    assert container_verdict(si_value, bl_value, si_text, bl_text) == expected


def test_container_verdict_never_raises_on_garbage():
    assert container_verdict(None, None, None, None) == "unverifiable"
    assert container_verdict(123, object(), None, [1, 2]) in {"agree", "differ", "unverifiable"}
