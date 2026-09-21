"""Re-check an amended draft Bill of Lading against the same SI.

OWNER: Ee Zhan.

The second half of the job. After the clerk sends the discrepancy note, the
carrier sends back a corrected draft. A human then has to check it again from
scratch, and the classic failure is that fixing one field quietly breaks
another. This compares SI against both drafts and sorts every field into:

    fixed         wrong in v1, right in v2
    still_wrong   wrong in both
    newly_broken  right in v1, wrong in v2   <- the one a tired clerk misses
    ok            right in both
    unreadable    could not be read in v2

No AI. It is compare() run twice, and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.compare import compare
from core.types import COMPARE_FIELDS, CompareField, ExtractedDoc, FieldComparison

OUTCOMES = ("newly_broken", "still_wrong", "unreadable", "fixed", "ok")


@dataclass(frozen=True, slots=True)
class RecheckRow:
    field: CompareField
    outcome: str
    v1: FieldComparison
    v2: FieldComparison


def _bad(c: FieldComparison) -> bool:
    return not c.matched and not c.undecidable


def recheck(si: ExtractedDoc, bl_v1: ExtractedDoc, bl_v2: ExtractedDoc) -> tuple[RecheckRow, ...]:
    """Seven rows in COMPARE_FIELDS order. Never raises (compare never does)."""
    first = {c.field: c for c in compare(si, bl_v1)}
    second = {c.field: c for c in compare(si, bl_v2)}
    rows = []
    for f in COMPARE_FIELDS:
        a, b = first[f], second[f]
        if b.undecidable:
            outcome = "unreadable"
        elif _bad(a) and b.matched:
            outcome = "fixed"
        elif _bad(a) and _bad(b):
            outcome = "still_wrong"
        elif not _bad(a) and _bad(b):
            outcome = "newly_broken"
        else:
            outcome = "ok"
        rows.append(RecheckRow(field=f, outcome=outcome, v1=a, v2=b))
    return tuple(rows)


def all_clear(rows: tuple[RecheckRow, ...]) -> bool:
    return all(r.outcome in ("fixed", "ok") for r in rows)
