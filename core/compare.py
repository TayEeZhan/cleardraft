"""Stage 3b: compare the SI against the BL. No AI, ever.

OWNER: Ee Zhan.

The SI is the reference. The BL is what the carrier typed. A field is a defect
when both sides are present, non-blank, and unequal after normalisation.

If either side is missing or blank the row is UNDECIDABLE. An undecidable row
escalates the email. It is never reported as a match and never as a defect.
"""
from __future__ import annotations

from core.normalise import normalise
from core.types import COMPARE_FIELDS, CompareField, ExtractedDoc, FieldComparison, FieldValue


def _field_value(doc: "ExtractedDoc | None", field: CompareField) -> "FieldValue | None":
    """Best-effort lookup of one field on a document, never raising.

    `doc` is typed as ExtractedDoc, but this module must not raise on any
    input, so a missing/garbage doc or fields mapping is treated the same
    as "field not present".
    """
    if doc is None:
        return None
    try:
        fields = doc.fields
    except Exception:
        return None
    if not fields:
        return None
    try:
        return fields.get(field)
    except Exception:
        return None


def _normalised(field: CompareField, fv: "FieldValue | None") -> "str | int | None":
    if fv is None:
        return None
    try:
        value = fv.value
    except Exception:
        return None
    try:
        return normalise(field, value)
    except Exception:
        return None


def compare(si: ExtractedDoc, bl: ExtractedDoc) -> "tuple[FieldComparison, ...]":
    """Always returns exactly seven rows, in COMPARE_FIELDS order."""
    rows: list[FieldComparison] = []
    for field in COMPARE_FIELDS:
        si_fv = _field_value(si, field)
        bl_fv = _field_value(bl, field)

        si_norm = _normalised(field, si_fv)
        bl_norm = _normalised(field, bl_fv)

        # A side is "missing or blank" whenever it has no field value at all,
        # or normalisation reduced it to None (absent / a blank token such as
        # "TBA" or "").
        si_absent = si_fv is None or si_norm is None
        bl_absent = bl_fv is None or bl_norm is None

        undecidable = si_absent or bl_absent
        matched = (not undecidable) and (si_norm == bl_norm)

        rows.append(
            FieldComparison(
                field=field,
                si=si_fv,
                bl=bl_fv,
                si_norm=si_norm,
                bl_norm=bl_norm,
                matched=matched,
                undecidable=undecidable,
            )
        )
    return tuple(rows)
