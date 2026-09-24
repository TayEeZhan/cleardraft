"""Pure core tests for learned equivalences: core/equivalence.py (the
eligibility rule and the lookup builder) and core/compare.py's known_equal
parameter.

No I/O anywhere in this file - these are what the guardrails in the plan
are about, so each one is its own test rather than folded into a bigger
scenario.
"""
from __future__ import annotations

from core.compare import compare
from core.equivalence import (
    EQUIVALENCE_FIELDS,
    build_pair_index,
    can_learn,
    evaluate_case,
    make_lookup,
    pair_key,
)
from core.types import COMPARE_FIELDS, ExtractedDoc, FieldValue


def _fv(value: str, label: str = "Field") -> FieldValue:
    return FieldValue(value=value, raw=f"{label}: {value}", line_no=1, label=label)


def _doc(kind: str, **fields: str) -> ExtractedDoc:
    return ExtractedDoc(path=f"x_{kind}.txt", kind=kind, fields={k: _fv(v) for k, v in fields.items()})


# ---------------------------------------------------------------------------
# EQUIVALENCE_FIELDS
# ---------------------------------------------------------------------------
def test_equivalence_fields_are_the_five_text_fields_only():
    assert set(EQUIVALENCE_FIELDS) == {
        "shipper", "consignee", "notify_party", "port_of_loading", "port_of_discharge",
    }
    assert "container_count" not in EQUIVALENCE_FIELDS
    assert "gross_weight_kg" not in EQUIVALENCE_FIELDS
    # every learnable field really is one of the seven compared fields
    assert set(EQUIVALENCE_FIELDS) <= set(COMPARE_FIELDS)


# ---------------------------------------------------------------------------
# pair_key: order-independent identity
# ---------------------------------------------------------------------------
def test_pair_key_is_order_independent():
    assert pair_key("consignee", "A", "B") == pair_key("consignee", "B", "A")


def test_pair_key_is_field_scoped():
    assert pair_key("consignee", "A", "B") != pair_key("shipper", "A", "B")


def test_pair_key_distinguishes_different_values():
    assert pair_key("consignee", "A", "B") != pair_key("consignee", "A", "C")


# ---------------------------------------------------------------------------
# can_learn
# ---------------------------------------------------------------------------
def test_can_learn_true_for_an_eligible_row():
    assert can_learn("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA", False) is True


def test_can_learn_false_for_numeric_fields_even_if_everything_else_fits():
    assert can_learn("container_count", "6", "7", False) is False
    assert can_learn("gross_weight_kg", "131058", "132058", False) is False


def test_can_learn_false_when_undecidable():
    assert can_learn("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA", True) is False


def test_can_learn_false_when_either_side_blank_or_not_a_string():
    assert can_learn("consignee", None, "UAB NOVAKOPA", False) is False
    assert can_learn("consignee", "EAST BRIGHT FZ-LLC", None, False) is False
    assert can_learn("consignee", "", "UAB NOVAKOPA", False) is False


def test_can_learn_false_when_already_equal():
    assert can_learn("consignee", "UAB NOVAKOPA", "UAB NOVAKOPA", False) is False


def test_can_learn_false_for_unknown_field():
    assert can_learn("bogus_field", "A", "B", False) is False


# ---------------------------------------------------------------------------
# make_lookup
# ---------------------------------------------------------------------------
def test_make_lookup_matches_the_taught_pair_either_order():
    lookup = make_lookup([{"field": "consignee", "a": "EAST BRIGHT FZ-LLC", "b": "UAB NOVAKOPA"}])
    assert lookup("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA") is True
    assert lookup("consignee", "UAB NOVAKOPA", "EAST BRIGHT FZ-LLC") is True


def test_make_lookup_does_not_match_a_different_field():
    lookup = make_lookup([{"field": "consignee", "a": "A", "b": "B"}])
    assert lookup("notify_party", "A", "B") is False


def test_make_lookup_does_not_match_an_untaught_pair():
    lookup = make_lookup([{"field": "consignee", "a": "A", "b": "B"}])
    assert lookup("consignee", "A", "C") is False


def test_make_lookup_ignores_malformed_records():
    lookup = make_lookup([{"field": "consignee"}, {"a": "A", "b": "B"}, "not a dict", None])
    assert lookup("consignee", "A", "B") is False


def test_make_lookup_never_raises_on_non_string_input():
    lookup = make_lookup([{"field": "consignee", "a": "A", "b": "B"}])
    assert lookup("consignee", None, None) is False
    assert lookup("consignee", 123, "B") is False


# ---------------------------------------------------------------------------
# core.compare.compare(): known_equal integration
# ---------------------------------------------------------------------------
def test_known_equal_none_is_byte_identical_to_before():
    """The scored path (run_pipeline.py, export_ui_data.py) never passes
    known_equal. With it omitted, behaviour must be unchanged."""
    si = _doc("SI", consignee="EAST BRIGHT FZ-LLC")
    bl = _doc("BL", consignee="UAB NOVAKOPA")
    without_kw = compare(si, bl)
    with_none = compare(si, bl, known_equal=None)
    assert without_kw == with_none
    consignee = next(r for r in with_none if r.field == "consignee")
    assert consignee.matched is False


def test_learned_pair_clears_only_that_exact_pair_for_that_field():
    lookup = make_lookup([{"field": "consignee", "a": "EAST BRIGHT FZ-LLC", "b": "UAB NOVAKOPA"}])
    si = _doc("SI", consignee="EAST BRIGHT FZ-LLC", notify_party="EAST BRIGHT FZ-LLC")
    bl = _doc("BL", consignee="UAB NOVAKOPA", notify_party="UAB NOVAKOPA")
    rows = {r.field: r for r in compare(si, bl, known_equal=lookup)}
    assert rows["consignee"].matched is True


def test_same_two_strings_under_a_different_field_do_not_clear():
    """A pair taught for consignee must not silently also clear notify_party,
    even though both rows hold the exact same two strings."""
    lookup = make_lookup([{"field": "consignee", "a": "EAST BRIGHT FZ-LLC", "b": "UAB NOVAKOPA"}])
    si = _doc("SI", consignee="EAST BRIGHT FZ-LLC", notify_party="EAST BRIGHT FZ-LLC")
    bl = _doc("BL", consignee="UAB NOVAKOPA", notify_party="UAB NOVAKOPA")
    rows = {r.field: r for r in compare(si, bl, known_equal=lookup)}
    assert rows["consignee"].matched is True
    assert rows["notify_party"].matched is False


def test_reversed_order_still_clears():
    lookup = make_lookup([{"field": "consignee", "a": "UAB NOVAKOPA", "b": "EAST BRIGHT FZ-LLC"}])
    si = _doc("SI", consignee="EAST BRIGHT FZ-LLC")
    bl = _doc("BL", consignee="UAB NOVAKOPA")
    rows = {r.field: r for r in compare(si, bl, known_equal=lookup)}
    assert rows["consignee"].matched is True


def test_numeric_fields_never_learn_or_clear_even_if_lookup_says_yes():
    """A known_equal that (wrongly, or maliciously) says yes for a numeric
    field must be ignored - compare() only ever asks it about the five text
    fields."""
    always_yes = lambda field, a, b: True  # noqa: E731
    si = _doc("SI", container_count="6", gross_weight_kg="131058")
    bl = _doc("BL", container_count="7", gross_weight_kg="132058")
    rows = {r.field: r for r in compare(si, bl, known_equal=always_yes)}
    assert rows["container_count"].matched is False
    assert rows["gross_weight_kg"].matched is False


def test_undecidable_rows_never_change_even_with_a_permissive_lookup():
    always_yes = lambda field, a, b: True  # noqa: E731
    si = _doc("SI")  # no fields at all -> every row undecidable
    bl = _doc("BL", consignee="UAB NOVAKOPA")
    rows = {r.field: r for r in compare(si, bl, known_equal=always_yes)}
    consignee = rows["consignee"]
    assert consignee.undecidable is True
    assert consignee.matched is False


def test_known_equal_that_raises_is_treated_as_no_match():
    def boom(field, a, b):
        raise RuntimeError("boom")

    si = _doc("SI", consignee="EAST BRIGHT FZ-LLC")
    bl = _doc("BL", consignee="UAB NOVAKOPA")
    rows = compare(si, bl, known_equal=boom)
    consignee = next(r for r in rows if r.field == "consignee")
    assert consignee.matched is False


def test_known_equal_only_ever_downgrades_never_upgrades():
    """A known_equal that claims two ALREADY-MATCHING values are equal
    changes nothing - matched was already True on the si_norm == bl_norm
    branch, and a mismatch can never be turned into... a mismatch by this
    path either. This pins that the parameter can only ever move a row from
    mismatch to match, never the reverse."""
    always_false = lambda field, a, b: False  # noqa: E731
    si = _doc("SI", consignee="UAB NOVAKOPA")
    bl = _doc("BL", consignee="UAB NOVAKOPA")
    rows = compare(si, bl, known_equal=always_false)
    consignee = next(r for r in rows if r.field == "consignee")
    assert consignee.matched is True


# ---------------------------------------------------------------------------
# build_pair_index
# ---------------------------------------------------------------------------
def test_build_pair_index_maps_pair_key_to_id():
    pairs = [{"id": "p1", "field": "consignee", "a": "A", "b": "B"}]
    index = build_pair_index(pairs)
    assert index[pair_key("consignee", "A", "B")] == "p1"


def test_build_pair_index_skips_malformed_records():
    pairs = [{"field": "consignee"}, {"a": "A", "b": "B", "id": "x"}, "nope", None]
    assert build_pair_index(pairs) == {}


# ---------------------------------------------------------------------------
# evaluate_case: the one rule
# ---------------------------------------------------------------------------
def _row(field, si_value, bl_value, matched=False, undecidable=False, learned=False):
    return {
        "field": field,
        "matched": matched,
        "undecidable": undecidable,
        "learned": learned,
        "si": {"value": si_value} if si_value is not None else None,
        "bl": {"value": bl_value} if bl_value is not None else None,
    }


def _comparison_case(email_id, status, defect_fields, comparisons, category="BL_COMPARISON", review_reason=None):
    return {
        "email_id": email_id,
        "status": status,
        "category": category,
        "review_reason": review_reason,
        "defect_fields": defect_fields,
        "has_defect": bool(defect_fields),
        "comparisons": comparisons,
    }


def test_covered_row_leaves_case_status_but_drops_from_defects_when_others_remain():
    comparisons = [
        _row("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA"),
        _row("notify_party", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA"),
    ]
    case = _comparison_case("e1", "MISMATCH", ["consignee", "notify_party"], comparisons)
    index = build_pair_index([
        {"id": "p1", "field": "consignee", "a": "EAST BRIGHT FZ-LLC", "b": "UAB NOVAKOPA"},
    ])
    result = evaluate_case(case, index)
    assert result["status"] == "MISMATCH"
    assert result["defect_fields"] == ["notify_party"]
    assert result["rows"] == {"consignee": "p1"}
    assert result["changed"] is True


def test_mismatch_becomes_ok_only_when_all_differing_rows_covered():
    comparisons = [
        _row("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA"),
        _row("notify_party", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA"),
    ]
    case = _comparison_case("e1", "MISMATCH", ["consignee", "notify_party"], comparisons)
    index = build_pair_index([
        {"id": "p1", "field": "consignee", "a": "EAST BRIGHT FZ-LLC", "b": "UAB NOVAKOPA"},
        {"id": "p2", "field": "notify_party", "a": "EAST BRIGHT FZ-LLC", "b": "UAB NOVAKOPA"},
    ])
    result = evaluate_case(case, index)
    assert result["status"] == "OK"
    assert result["defect_fields"] == []
    assert result["has_defect"] is False
    assert result["changed"] is True


def test_needs_review_case_is_left_untouched():
    case = {
        "email_id": "e2",
        "status": "NEEDS_REVIEW",
        "category": "BL_COMPARISON",
        "review_reason": "unreadable",
        "defect_fields": [],
        "has_defect": False,
        "comparisons": [],
    }
    index = build_pair_index([{"id": "p1", "field": "consignee", "a": "A", "b": "B"}])
    result = evaluate_case(case, index)
    assert result["status"] == "NEEDS_REVIEW"
    assert result["changed"] is False


def test_non_comparison_case_is_left_untouched():
    case = {
        "email_id": "e3",
        "status": "OK",
        "category": "GENERAL",
        "review_reason": None,
        "defect_fields": [],
        "has_defect": False,
        "comparisons": [],
    }
    result = evaluate_case(case, {})
    assert result["status"] == "OK"
    assert result["changed"] is False


def test_learned_row_without_a_covering_pair_counts_as_defect_again():
    """A row already marked `learned: true` (matched=True) with no pair on
    record any more (removed) is still a defect - `differs` looks past
    `matched` at `learned`."""
    comparisons = [_row("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA", matched=True, learned=True)]
    case = _comparison_case("e4", "OK", [], comparisons)
    result = evaluate_case(case, {})  # no pairs on record
    assert result["status"] == "MISMATCH"
    assert result["defect_fields"] == ["consignee"]
    assert result["changed"] is True


def test_numeric_field_row_never_covered():
    comparisons = [_row("container_count", "6", "7")]
    case = _comparison_case("e5", "MISMATCH", ["container_count"], comparisons)
    index = build_pair_index([{"id": "p1", "field": "container_count", "a": "6", "b": "7"}])
    result = evaluate_case(case, index)
    assert result["defect_fields"] == ["container_count"]
    assert result["rows"] == {}


def test_undecidable_row_never_covered():
    comparisons = [_row("consignee", None, "UAB NOVAKOPA", undecidable=True)]
    case = _comparison_case("e6", "NEEDS_REVIEW", [], comparisons, review_reason="missing_value")
    index = build_pair_index([{"id": "p1", "field": "consignee", "a": "A", "b": "UAB NOVAKOPA"}])
    result = evaluate_case(case, index)
    assert result["rows"] == {}


def test_covered_row_is_field_scoped():
    comparisons = [_row("notify_party", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA")]
    case = _comparison_case("e7", "MISMATCH", ["notify_party"], comparisons)
    index = build_pair_index([{"id": "p1", "field": "consignee", "a": "EAST BRIGHT FZ-LLC", "b": "UAB NOVAKOPA"}])
    result = evaluate_case(case, index)
    assert result["defect_fields"] == ["notify_party"]


def test_covered_row_is_order_independent():
    comparisons = [_row("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA")]
    case = _comparison_case("e8", "MISMATCH", ["consignee"], comparisons)
    index = build_pair_index([{"id": "p1", "field": "consignee", "a": "UAB NOVAKOPA", "b": "EAST BRIGHT FZ-LLC"}])
    result = evaluate_case(case, index)
    assert result["defect_fields"] == []


def test_recheck_still_wrong_and_newly_broken_covered_become_ok():
    case = {
        "email_id": "e9",
        "status": "OK",
        "category": "GENERAL",
        "review_reason": None,
        "defect_fields": [],
        "has_defect": False,
        "comparisons": [],
        "recheck": {
            "rows": [
                {"field": "notify_party", "outcome": "still_wrong", "si": "EAST BRIGHT FZ-LLC", "v1": "UAB NOVAKOPA", "v2": "UAB NOVAKOPA"},
                {"field": "container_count", "outcome": "newly_broken", "si": "6", "v1": "6", "v2": "5"},
                {"field": "shipper", "outcome": "ok", "si": "A", "v1": "A", "v2": "A"},
            ]
        },
    }
    index = build_pair_index([
        {"id": "p1", "field": "notify_party", "a": "EAST BRIGHT FZ-LLC", "b": "UAB NOVAKOPA"},
    ])
    result = evaluate_case(case, index)
    assert result["recheck"]["notify_party"] == {"outcome": "ok", "pair_id": "p1"}
    # container_count is not a learnable field, so never covered
    assert "container_count" not in result["recheck"]
    assert result["changed"] is True


def test_recheck_unrelated_outcomes_never_change():
    case = {
        "email_id": "e10",
        "status": "OK",
        "category": "GENERAL",
        "review_reason": None,
        "defect_fields": [],
        "has_defect": False,
        "comparisons": [],
        "recheck": {"rows": [{"field": "shipper", "outcome": "fixed", "si": "A", "v1": "B", "v2": "A"}]},
    }
    index = build_pair_index([{"id": "p1", "field": "shipper", "a": "A", "b": "B"}])
    result = evaluate_case(case, index)
    assert result["recheck"] == {}
    assert result["changed"] is False


def test_evaluate_case_never_raises_on_garbage():
    assert evaluate_case({}, {})["status"] is None
    assert evaluate_case({"comparisons": "not a list"}, {})["changed"] is False
