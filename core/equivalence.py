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

from core.types import CompareField

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
MAX_VALUE_LENGTH = 200
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


__all__ = [
    "EQUIVALENCE_FIELDS",
    "MAX_VALUE_LENGTH",
    "MAX_PAIRS_PER_ACCOUNT",
    "pair_key",
    "make_lookup",
    "can_learn",
]
