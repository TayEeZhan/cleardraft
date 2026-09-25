"""Integration tests: core/compare.py's container_count gate (wired to
core.containers.container_verdict). Pins the exact decision table from the
container-composition fix, run through compare() end-to-end - not just the
pure container_verdict function tested in tests/test_containers.py.
"""
from __future__ import annotations

from core.compare import compare
from core.types import ExtractedDoc, FieldValue


def _fv(value: str) -> FieldValue:
    return FieldValue(value=value, raw=f"Container Count: {value}", line_no=1, label="Container Count")


def _doc(kind: str, container_count: str, text: str = "") -> ExtractedDoc:
    return ExtractedDoc(
        path=f"x_{kind}.txt",
        kind=kind,
        fields={"container_count": _fv(container_count)},
        text=text,
    )


def _row(si: ExtractedDoc, bl: ExtractedDoc):
    rows = {r.field: r for r in compare(si, bl)}
    return rows["container_count"]


def test_identical_mixed_equipment_string_is_now_a_match():
    """Previously NEEDS_REVIEW: a false escalation on two identical strings."""
    si = _doc("SI", "2 x 40HC + 1 x 20GP")
    bl = _doc("BL", "2 x 40HC + 1 x 20GP")
    row = _row(si, bl)
    assert row.matched is True
    assert row.undecidable is False


def test_same_total_different_size_is_now_a_mismatch():
    """Previously a silent OK: six 40' boxes vs six 20' boxes is roughly
    half the cargo volume."""
    si = _doc("SI", "6 x 40'HC")
    bl = _doc("BL", "6 x 20GP")
    row = _row(si, bl)
    assert row.matched is False
    assert row.undecidable is False


def test_same_total_different_split_is_a_mismatch():
    si = _doc("SI", "2 x 40HC + 1 x 20GP")
    bl = _doc("BL", "1 x 40HC + 2 x 20GP")
    row = _row(si, bl)
    assert row.matched is False
    assert row.undecidable is False


def test_different_totals_are_a_mismatch():
    si = _doc("SI", "6 x 40'HC")
    bl = _doc("BL", "5 x 40'HC")
    row = _row(si, bl)
    assert row.matched is False
    assert row.undecidable is False


def test_bare_count_matches_when_the_bl_states_the_size_elsewhere():
    si = _doc("SI", "6 x 40'HC")
    bl = _doc("BL", "6", text="SIX CONTAINERS SAID TO CONTAIN 6 X 40'HC.")
    row = _row(si, bl)
    assert row.matched is True
    assert row.undecidable is False


def test_bare_count_is_now_needs_review_when_no_size_is_stated_anywhere():
    """Previously a silent OK: the equipment was never actually verified."""
    si = _doc("SI", "6 x 40'HC")
    bl = _doc("BL", "6", text="SIX CONTAINERS SAID TO CONTAIN GENERAL CARGO.")
    row = _row(si, bl)
    assert row.matched is False
    assert row.undecidable is True


def test_both_bare_counts_with_no_size_anywhere_still_match():
    si = _doc("SI", "6", text="SIX CONTAINERS.")
    bl = _doc("BL", "6", text="SIX CONTAINERS.")
    row = _row(si, bl)
    assert row.matched is True
    assert row.undecidable is False


def test_ambiguous_size_first_order_is_unchanged_needs_review():
    si = _doc("SI", "40HC x 2")
    bl = _doc("BL", "2")
    row = _row(si, bl)
    assert row.matched is False
    assert row.undecidable is True


def test_spelled_out_multiplier_is_unchanged_needs_review():
    si = _doc("SI", "TWO x 40HC")
    bl = _doc("BL", "2")
    row = _row(si, bl)
    assert row.matched is False
    assert row.undecidable is True


def test_blank_bl_value_is_unchanged_needs_review():
    si = _doc("SI", "6")
    bl = _doc("BL", "???")
    row = _row(si, bl)
    assert row.matched is False
    assert row.undecidable is True
