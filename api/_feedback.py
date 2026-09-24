"""Per-account human-review feedback storage.

The browser remains local-first, but signed-in reviews are mirrored here so
they survive another device/browser and can be applied to report exports.
Pipeline results remain immutable; this router stores only the review overlay.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from adapters.store import Store, get_store
from api._accounts import current_user
from core.feedback import is_problem_feedback

router = APIRouter()

ALLOWED_VERDICTS = frozenset({"confirmed", "flagged", "done"})
MAX_KEY_LENGTH = 300
MAX_NOTE_LENGTH = 1000
MAX_REVIEWS_PER_ACCOUNT = 2000


class FeedbackRequest(BaseModel):
    key: str
    verdict: str
    note: str = ""


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

    reviews = _load_reviews(store, email)
    if key not in reviews and len(reviews) >= MAX_REVIEWS_PER_ACCOUNT:
        return _error(400, "too_many_reviews", f"You can save at most {MAX_REVIEWS_PER_ACCOUNT} reviews.")

    record = {
        "verdict": payload.verdict,
        "note": note,
        "at": datetime.now(timezone.utc).isoformat(),
    }
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
