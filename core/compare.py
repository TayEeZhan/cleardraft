"""Stage 3b: compare the SI against the BL. No AI, ever.

OWNER: Ee Zhan.

The SI is the reference. The BL is what the carrier typed. A field is a defect
when both sides are present, non-blank, and unequal after normalisation.

If either side is missing or blank the row is UNDECIDABLE. An undecidable row
escalates the email. It is never reported as a match and never as a defect.
"""
from __future__ import annotations

from typing import Callable

from core.equivalence import EQUIVALENCE_FIELDS
from core.normalise import normalise
from core.types import COMPARE_FIELDS, CompareField, ExtractedDoc, FieldComparison, FieldValue
from core.units import describe_unit_difference


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


def _safe_known_equal(
    known_equal: "Callable[[str, object, object], bool]",
    field: CompareField,
    si_norm: "str | int | None",
    bl_norm: "str | int | None",
) -> bool:
    """Call a caller-supplied known_equal, never letting it raise. A learned
    pair downgrading a mismatch is a nicety; a crash here must never take
    down a comparison the organiser's scorer depends on."""
    try:
        return bool(known_equal(field, si_norm, bl_norm))
    except Exception:
        return False


def compare(
    si: ExtractedDoc,
    bl: ExtractedDoc,
    *,
    known_equal: "Callable[[str, object, object], bool] | None" = None,
) -> "tuple[FieldComparison, ...]":
    """Always returns exactly seven rows, in COMPARE_FIELDS order.

    `known_equal`, when given, is asked - only for the five learnable text
    fields, only when both sides are decided - whether a clerk has already
    approved this exact SI/BL pair as the same. With known_equal=None
    (scripts/run_pipeline.py, export_ui_data.py, every caller that scores
    the pipeline) behaviour is byte-identical to before this parameter
    existed.
    """
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
        matched = (not undecidable) and (
            si_norm == bl_norm
            or (
                known_equal is not None
                and field in EQUIVALENCE_FIELDS
                and _safe_known_equal(known_equal, field, si_norm, bl_norm)
            )
        )

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


def compare_values(field: str, si: str, bl: str) -> dict:
    """Re-check two RAW values a clerk typed for one field, with the exact
    same rule a row read off a document gets in compare() above: normalise
    both sides, then decide by exact equality after normalisation. No AI,
    no fuzzy threshold, no equivalence-pair lookup (a "Fix value" correction
    is a one-off recheck of what the clerk typed, not a taught pair).

    Used by api/_recheck.py's POST /api/recheck-field, and never called
    directly by the pipeline - it exists so a clerk's typed correction is
    checked by the SAME deterministic code the pipeline already trusts,
    not a second copy of the rule.

    Returns:
        {
            "status": "match" | "mismatch" | "undecidable",
            "si_normalised": str | int | None,
            "bl_normalised": str | int | None,
            "note": str | None,   # unit-conversion note, gross_weight_kg only
        }

    Never raises: an unknown field, or normalise()/describe_unit_difference()
    raising on unexpected input, all collapse to "undecidable" with no note,
    the same fail-closed discipline as compare() itself.
    """
    try:
        si_norm = normalise(field, si) if field in COMPARE_FIELDS else None
    except Exception:
        si_norm = None
    try:
        bl_norm = normalise(field, bl) if field in COMPARE_FIELDS else None
    except Exception:
        bl_norm = None

    if si_norm is None or bl_norm is None:
        status = "undecidable"
    elif si_norm == bl_norm:
        status = "match"
    else:
        status = "mismatch"

    note = None
    if field == "gross_weight_kg":
        try:
            note = describe_unit_difference(si, bl)
        except Exception:
            note = None

    return {
        "status": status,
        "si_normalised": si_norm,
        "bl_normalised": bl_norm,
        "note": note,
    }
