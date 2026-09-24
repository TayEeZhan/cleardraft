"""Per-account human-review feedback storage.

The browser remains local-first, but signed-in reviews are mirrored here so
they survive another device/browser and can be applied to report exports.
Pipeline results remain immutable; this router stores only the review overlay.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from adapters.store import Store, get_store
from api._accounts import current_user
from core.feedback import is_problem_feedback
from core.types import COMPARE_FIELDS

router = APIRouter()

ALLOWED_VERDICTS = frozenset({"confirmed", "flagged", "done"})
MAX_KEY_LENGTH = 300
MAX_NOTE_LENGTH = 1000
MAX_REVIEWS_PER_ACCOUNT = 2000

#: "Fix a value": additive to the review record. One correction per field
#: (si + bl, as the clerk typed them after "Check again"), at most the seven
#: compared fields, each value bounded the same as api/_recheck.py's request
#: cap - this is the account-mirrored copy of what the browser already saved
#: to localStorage, not a new source of truth.
MAX_CORRECTION_VALUE_LENGTH = 500


class CorrectionValue(BaseModel):
    si: str = ""
    bl: str = ""


class FeedbackRequest(BaseModel):
    key: str
    verdict: str
    note: str = ""
    corrections: "Optional[dict[str, CorrectionValue]]" = None


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


def _feedback_key(email: str) -> str:
    return f"feedback:{email}"


def _load_reviews(store: Store, email: str) -> dict:
    raw = store.get(_feedback_key(email))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _save_reviews(store: Store, email: str, reviews: dict) -> None:
    store.set(_feedback_key(email), json.dumps(reviews))


def _authenticated(request: Request):
    store = get_store()
    if store is None:
        return None, None, _error(
            503,
            "accounts_unavailable",
            "Accounts are not configured on this deployment.",
        )
    email = current_user(request)
    if email is None:
        return store, None, _error(401, "not_signed_in", "Sign in to continue.")
    return store, email, None


@router.get("/api/feedback")
async def list_feedback(request: Request):
    store, email, error = _authenticated(request)
    if error is not None:
        return error
    return {"reviews": _load_reviews(store, email)}


@router.put("/api/feedback")
async def save_feedback(payload: FeedbackRequest, request: Request):
    store, email, error = _authenticated(request)
    if error is not None:
        return error

    key = payload.key.strip()
    note = payload.note.strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        return _error(400, "invalid_key", f"Review keys must be 1-{MAX_KEY_LENGTH} characters.")
    if payload.verdict not in ALLOWED_VERDICTS:
        return _error(400, "invalid_verdict", "Unknown review verdict.")
    if len(note) > MAX_NOTE_LENGTH:
        return _error(400, "note_too_long", f"Notes must be {MAX_NOTE_LENGTH} characters or fewer.")

    candidate = {"verdict": payload.verdict, "note": note}
    if payload.verdict == "flagged" and not note:
        return _error(400, "note_required", "Explain what is wrong before saving.")
    if payload.verdict == "done" and note.lower().startswith("problem:") and not is_problem_feedback(candidate):
        return _error(400, "note_required", "Explain the problem after 'problem:'.")

    corrections = None
    if payload.corrections is not None:
        if len(payload.corrections) > len(COMPARE_FIELDS):
            return _error(
                400,
                "invalid_corrections",
                f"At most {len(COMPARE_FIELDS)} field corrections per case.",
            )
        corrections = {}
        for field, value in payload.corrections.items():
            if field not in COMPARE_FIELDS:
                return _error(400, "invalid_corrections", f"{field!r} is not one of the seven compared fields.")
            if len(value.si) > MAX_CORRECTION_VALUE_LENGTH or len(value.bl) > MAX_CORRECTION_VALUE_LENGTH:
                return _error(
                    400,
                    "invalid_corrections",
                    f"Correction values must be {MAX_CORRECTION_VALUE_LENGTH} characters or fewer.",
                )
            corrections[field] = {"si": value.si, "bl": value.bl}

    reviews = _load_reviews(store, email)
    if key not in reviews and len(reviews) >= MAX_REVIEWS_PER_ACCOUNT:
        return _error(400, "too_many_reviews", f"You can save at most {MAX_REVIEWS_PER_ACCOUNT} reviews.")

    record = {
        "verdict": payload.verdict,
        "note": note,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    # Additive only: a PUT that omits `corrections` (every review save before
    # this feature existed, and every one from a client that doesn't send it)
    # must not erase corrections a previous save already recorded for this
    # same key - so an absent field keeps whatever was there, and only an
    # explicit (possibly empty) `corrections` object ever replaces it.
    if corrections is not None:
        record["corrections"] = corrections
    else:
        existing = reviews.get(key)
        if isinstance(existing, dict) and isinstance(existing.get("corrections"), dict):
            record["corrections"] = existing["corrections"]

    reviews[key] = record
    _save_reviews(store, email, reviews)
    return {"review": record}


@router.delete("/api/feedback/{review_key:path}")
async def delete_feedback(review_key: str, request: Request):
    store, email, error = _authenticated(request)
    if error is not None:
        return error
    reviews = _load_reviews(store, email)
    deleted = review_key in reviews
    reviews.pop(review_key, None)
    if deleted:
        _save_reviews(store, email, reviews)
    return {"deleted": deleted}
