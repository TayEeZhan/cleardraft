"""Fix a value: a clerk types a corrected SI/BL wording for one field, and it
is re-checked by the SAME deterministic normalise + compare rule the
pipeline uses. No AI, ever.

OWNER: whoever built this feature. A TRANSPORT LAYER ONLY, same discipline
as api/_equivalences.py - the comparison itself is core.compare.compare_values;
this module only reads the request, validates it, and serialises the result.

Route:
    POST /api/recheck-field   body {field, si_value, bl_value}

Nothing is stored here and no account is required: the clerk's saved
correction (if any) is persisted client-side (localStorage) or, when signed
in, mirrored into api/_feedback.py's review record - this endpoint is a
pure, stateless recheck, exactly like core.compare.compare() itself.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.compare import compare_values
from core.types import COMPARE_FIELDS
from core.variance import explain_difference, variance_label

router = APIRouter()

#: A raw SI/BL value a clerk types by hand. Generous enough for a full
#: company name plus its postal address on one line, small enough to keep
#: the request (and every persisted correction) bounded.
MAX_VALUE_LENGTH = 500


class RecheckFieldRequest(BaseModel):
    field: str
    si_value: str = ""
    bl_value: str = ""


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


@router.post("/api/recheck-field")
async def recheck_field(payload: RecheckFieldRequest):
    if payload.field not in COMPARE_FIELDS:
        return _error(
            400,
            "invalid_field",
            f"{payload.field!r} is not one of the seven compared fields.",
        )
    if len(payload.si_value) > MAX_VALUE_LENGTH or len(payload.bl_value) > MAX_VALUE_LENGTH:
        return _error(
            400,
            "value_too_long",
            f"Values must be {MAX_VALUE_LENGTH} characters or fewer.",
        )

    result = compare_values(payload.field, payload.si_value, payload.bl_value)
    response: dict[str, object] = {
        "field": payload.field,
        "status": result["status"],
        "si_normalised": result["si_normalised"],
        "bl_normalised": result["bl_normalised"],
        "note": result["note"],
    }
    # ADDITIVE: only a mismatch gets a "hint" key, so an existing match/
    # undecidable response body is byte-identical to before this field
    # existed (see tests/test_recheck_field.py's exact-body assertion).
    # Reuses core.variance.explain_difference - the same conservative,
    # display-only pattern matcher a saved case's seam table already uses
    # (core/variance.py:comparison_variance) - never a new heuristic, and
    # it never changes `status` above: a hint only explains a discrepancy,
    # it can never clear one (ADR-001).
    if result["status"] == "mismatch":
        try:
            reason = explain_difference(payload.field, payload.si_value, payload.bl_value)
        except Exception:
            reason = None
        response["hint"] = (
            {"kind": reason, "label": variance_label(reason)} if reason else None
        )
    return response


__all__ = ["router"]
