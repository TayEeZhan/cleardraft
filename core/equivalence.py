"""Learned equivalences: which fields can learn an exact pair, and how a
stored list of pairs becomes the `known_equal` callable core/compare.py
calls. Pure, no I/O - this module never touches a store, a request, or a
clock.

OWNER: Sheng Kuan.

A "pair" downgrades exactly one mismatch (one field, two exact normalised
strings, in either order) to a match. It is never a pattern, never a
wildcard, and never derived automatically - a human clicked "these are the
same" for this exact text, once.
"""
from __future__ import annotations

from typing import Callable, Iterable, Mapping

from core.normalise import normalise
from core.types import COMPARE_FIELDS, CompareField

#: Only these seven-row fields can ever be taught a pair. container_count
#: and gross_weight_kg are numeric - a difference there is always a real
#: defect, never a formatting quirk, so they are deliberately excluded.
EQUIVALENCE_FIELDS: "tuple[CompareField, ...]" = (
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
)

#: Bounds enforced by the API (api/_equivalences.py) when a pair is learned.
#: Kept here, next to the concept they bound, so the API and any future
#: caller share one source of truth instead of duplicating magic numbers.
#:
#: MAX_VALUE_LENGTH bounds the NORMALISED wording (what actually gets stored
#: and compared) - not the raw input a clerk pastes. A company name with its
#: postal address glued on ("KTP CO., LTD | KTP BLDG., ...; SEOUL") can run
#: well past 200 characters raw while normalising down to a short name, and
#: that must still be learnable. MAX_INPUT_LENGTH is the separate, much more
#: generous cap on the raw wording itself, just to keep a single request sane.
MAX_VALUE_LENGTH = 200
MAX_INPUT_LENGTH = 2000
MAX_NOTE_LENGTH = 280
MAX_PAIRS_PER_ACCOUNT = 500


def pair_key(field: str, a: str, b: str) -> str:
    """Order-independent identity for one pair on one field.

    Sorting the two normalised strings means "A same as B" and "B same as
    A" are the exact same pair - taught once, either order clears it.
    """
    lo, hi = sorted((a, b))
    return f"{field}|{lo}|{hi}"


def make_lookup(pairs: "Iterable[Mapping[str, object]]") -> "Callable[[str, object, object], bool]":
    """Build a `known_equal(field, si_norm, bl_norm) -> bool` callable from a
    list of stored pair records (each with at least "field", "a", "b" -
    already-normalised strings). A malformed record is skipped, never
    raised on, so one corrupt row can't take down every check.

    The returned callable itself never raises: a non-string si_norm/bl_norm
    (None, for instance - the "absent" sentinel) simply misses every pair.
    """
    keys: "set[str]" = set()
    for record in pairs:
        try:
            field = record["field"]
            a = record["a"]
            b = record["b"]
        except (KeyError, TypeError):
            continue
        if not isinstance(field, str) or not isinstance(a, str) or not isinstance(b, str):
            continue
        keys.add(pair_key(field, a, b))

    def known_equal(field: str, si_norm: object, bl_norm: object) -> bool:
        if not isinstance(si_norm, str) or not isinstance(bl_norm, str):
            return False
        return pair_key(field, si_norm, bl_norm) in keys

    return known_equal


def can_learn(field: str, si_norm: object, bl_norm: object, undecidable: bool) -> bool:
    """True only when a row is eligible to become a learned pair:
    an allowed (text) field, not undecidable, both sides normalise to a
    non-empty string, and the two strings actually differ (nothing to learn
    from a pair that already matches).
    """
    if field not in EQUIVALENCE_FIELDS:
        return False
    if undecidable:
        return False
    if not isinstance(si_norm, str) or not si_norm:
        return False
    if not isinstance(bl_norm, str) or not bl_norm:
        return False
    if si_norm == bl_norm:
        return False
    return True


def build_pair_index(pairs: "Iterable[Mapping[str, object]]") -> "dict[str, str]":
    """pair_key -> pair id, from a list of stored pair records.

    A malformed record (missing/wrong-typed field, a, b or id) is skipped,
    never raised on - the same discipline as make_lookup, since this reads
    the exact same storage shape.
    """
    index: "dict[str, str]" = {}
    for record in pairs:
        try:
            field = record["field"]
            a = record["a"]
            b = record["b"]
            pair_id = record["id"]
        except (KeyError, TypeError):
            continue
        if not (isinstance(field, str) and isinstance(a, str) and isinstance(b, str) and isinstance(pair_id, str)):
            continue
        index[pair_key(field, a, b)] = pair_id
    return index


def _row_norm(field: str, row: "Mapping[str, object] | None", side: str) -> "str | None":
    """normalise(field, row[side]['value']) - or None on any problem at all.

    Never raises: a missing side, a missing 'value' key, a non-string value,
    or normalise() itself failing all collapse to "not coverable", exactly
    like an undecidable row would.
    """
    if not isinstance(row, Mapping):
        return None
    try:
        side_obj = row.get(side)
    except Exception:
        return None
    if not isinstance(side_obj, Mapping):
        return None
    try:
        value = side_obj.get("value")
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    try:
        result = normalise(field, value)
    except Exception:
        return None
    return result if isinstance(result, str) else None


def _row_differs(row: "Mapping[str, object]") -> bool:
    try:
        undecidable = bool(row.get("undecidable", False))
        matched = bool(row.get("matched", False))
        learned = bool(row.get("learned", False))
    except Exception:
        return False
    return (not undecidable) and ((not matched) or learned)


def _coverage_for_row(field: str, row: "Mapping[str, object]", pair_index: "Mapping[str, str]") -> "str | None":
    """The covering pair id for one comparison row, or None."""
    if not _row_differs(row):
        return None
    if field not in EQUIVALENCE_FIELDS:
        return None
    si_norm = _row_norm(field, row, "si")
    bl_norm = _row_norm(field, row, "bl")
    if not si_norm or not bl_norm or si_norm == bl_norm:
        return None
    try:
        key = pair_key(field, si_norm, bl_norm)
    except Exception:
        return None
    return pair_index.get(key)


def _recheck_coverage(field: str, row: "Mapping[str, object]", pair_index: "Mapping[str, str]") -> "str | None":
    """The covering pair id for one recheck row (si/v2 are plain strings)."""
    try:
        si_raw = row.get("si")
        v2_raw = row.get("v2")
    except Exception:
        return None
    if not isinstance(si_raw, str) or not isinstance(v2_raw, str):
        return None
    try:
        si_norm = normalise(field, si_raw)
        v2_norm = normalise(field, v2_raw)
    except Exception:
        return None
    if not isinstance(si_norm, str) or not si_norm:
        return None
    if not isinstance(v2_norm, str) or not v2_norm:
        return None
    if si_norm == v2_norm:
        return None
    try:
        key = pair_key(field, si_norm, v2_norm)
    except Exception:
        return None
    return pair_index.get(key)


def evaluate_case(case: "Mapping[str, object]", pair_index: "Mapping[str, str]") -> dict:
    """Apply the one rule to one saved/checked case.

    Returns:
        {
            "email_id": str,
            "status": Status,               # effective
            "defect_fields": list[str],     # effective, COMPARE_FIELDS order
            "has_defect": bool,
            "changed": bool,
            "rows": {field: pair_id},       # covered comparison rows only
            "recheck": {field: {"outcome": str, "pair_id": str}},
        }

    Never raises: any malformed piece of `case` is treated as "not covered"
    or "nothing to change", the same never-fail discipline as compare().
    """
    try:
        email_id = str(case.get("email_id", ""))
    except Exception:
        email_id = ""

    checked_status = case.get("status")
    checked_defects = case.get("defect_fields") or []
    if not isinstance(checked_defects, (list, tuple)):
        checked_defects = []
    checked_defects = [f for f in checked_defects if isinstance(f, str)]

    comparisons = case.get("comparisons")
    category = case.get("category")
    review_reason = case.get("review_reason")

    covered_rows: "dict[str, str]" = {}
    is_comparison_case = (
        category == "BL_COMPARISON"
        and review_reason in (None, "")
        and checked_status in ("MISMATCH", "OK")
        and isinstance(comparisons, (list, tuple))
        and len(comparisons) > 0
    )

    if is_comparison_case:
        by_field: "dict[str, Mapping[str, object]]" = {}
        for row in comparisons:
            if isinstance(row, Mapping):
                f = row.get("field")
                if isinstance(f, str):
                    by_field[f] = row

        effective_defects: "list[str]" = []
        for f in COMPARE_FIELDS:
            row = by_field.get(f)
            if row is None:
                continue
            pair_id = _coverage_for_row(f, row, pair_index)
            if pair_id is not None:
                covered_rows[f] = pair_id
                continue
            if _row_differs(row):
                effective_defects.append(f)

        effective_status = "MISMATCH" if effective_defects else "OK"
        effective_has_defect = bool(effective_defects)
    else:
        effective_defects = list(checked_defects)
        effective_status = checked_status
        effective_has_defect = bool(case.get("has_defect", bool(checked_defects)))

    recheck_out: "dict[str, dict[str, str]]" = {}
    recheck_obj = case.get("recheck")
    recheck_changed = False
    if isinstance(recheck_obj, Mapping):
        rows = recheck_obj.get("rows")
        if isinstance(rows, (list, tuple)):
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                field = row.get("field")
                outcome = row.get("outcome")
                if not isinstance(field, str) or not isinstance(outcome, str):
                    continue
                if outcome not in ("still_wrong", "newly_broken"):
                    continue
                pair_id = _recheck_coverage(field, row, pair_index)
                if pair_id is not None:
                    recheck_out[field] = {"outcome": "ok", "pair_id": pair_id}
                    recheck_changed = True

    status_changed = effective_status != checked_status
    defects_changed = set(effective_defects) != set(checked_defects)
    changed = status_changed or defects_changed or recheck_changed

    ordered_defects = [f for f in COMPARE_FIELDS if f in effective_defects]

    return {
        "email_id": email_id,
        "status": effective_status,
        "defect_fields": ordered_defects,
        "has_defect": effective_has_defect,
        "changed": changed,
        "rows": covered_rows,
        "recheck": recheck_out,
    }


__all__ = [
    "EQUIVALENCE_FIELDS",
    "MAX_VALUE_LENGTH",
    "MAX_INPUT_LENGTH",
    "MAX_NOTE_LENGTH",
    "MAX_PAIRS_PER_ACCOUNT",
    "pair_key",
    "make_lookup",
    "can_learn",
    "build_pair_index",
    "evaluate_case",
]
