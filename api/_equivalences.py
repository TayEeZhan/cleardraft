"""Learned equivalences: a signed-in clerk marks one mismatched text field as
"the same"; that exact normalised pair, for that one field, is remembered on
their account and clears itself on their next check.

OWNER: whoever built this feature. A TRANSPORT + STORAGE layer only, same
discipline as api/_accounts.py - the eligibility rule itself
(core.equivalence.can_learn) and the lookup shape (core.equivalence.
make_lookup) are pure core code; this module only reads the request, checks
who is signed in, and talks to the store.

Storage: adapters/store.py's Store protocol, key `equiv:<email>` -> a JSON
list of pair records. No new storage backend - this reuses exactly what
api/_accounts.py already uses for users/sessions/mail.

Routes:
    GET    /api/equivalences        - list this account's learned pairs
    POST   /api/equivalences        - learn a new pair (idempotent on repeat)
    DELETE /api/equivalences/{id}   - undo one pair

`lookup_for(request)` is what api/index.py passes as compare()'s
known_equal: None when signed out or the store is unavailable, a callable
otherwise. It never raises - a store hiccup here must never break a check
that would otherwise have worked.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from adapters.store import Store, StoreError, get_store
from api._accounts import current_user
from core.equivalence import (
    EQUIVALENCE_FIELDS,
    MAX_PAIRS_PER_ACCOUNT,
    MAX_VALUE_LENGTH,
    can_learn,
    make_lookup,
    pair_key,
)
from core.normalise import normalise

router = APIRouter()


class LearnRequest(BaseModel):
    field: str
    si_value: str
    bl_value: str
    source: str = ""


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


# ---------------------------------------------------------------------------
# Storage: equiv:<email> -> JSON list of pair records
# ---------------------------------------------------------------------------
def _equiv_key(email: str) -> str:
    return f"equiv:{email}"


def _load_pairs(store: Store, email: str) -> "list[dict]":
    raw = store.get(_equiv_key(email))
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _save_pairs(store: Store, email: str, pairs: "list[dict]") -> None:
    store.set(_equiv_key(email), json.dumps(pairs))


def _find(pairs: "list[dict]", field: str, si_norm: str, bl_norm: str) -> "dict | None":
    key = pair_key(field, si_norm, bl_norm)
    for record in pairs:
        if record.get("field") != field:
            continue
        a, b = record.get("a"), record.get("b")
        if not isinstance(a, str) or not isinstance(b, str):
            continue
        if pair_key(field, a, b) == key:
            return record
    return None


# ---------------------------------------------------------------------------
# known_equal for core.compare.compare()
# ---------------------------------------------------------------------------
def lookup_for(request: Request) -> "Callable[[str, object, object], bool] | None":
    """The known_equal callable for this request's signed-in user, or None
    when signed out or accounts are unavailable. Never raises: a StoreError
    while loading pairs is treated the same as "no learned pairs yet" so a
    store hiccup degrades a check to today's behaviour instead of failing it.
    """
    store = get_store()
    if store is None:
        return None
    email = current_user(request)
    if email is None:
        return None
    try:
        pairs = _load_pairs(store, email)
    except StoreError:
        return None
    return make_lookup(pairs)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/api/equivalences")
async def list_equivalences(request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = current_user(request)
    if email is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    return {"pairs": _load_pairs(store, email)}


@router.post("/api/equivalences")
async def learn_equivalence(payload: LearnRequest, request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = current_user(request)
    if email is None:
        return _error(401, "not_signed_in", "Sign in to teach ClearDraft.")

    field = payload.field
    if field not in EQUIVALENCE_FIELDS:
        return _error(400, "disallowed_field", f"{field!r} cannot be taught a pair.")

    si_value = payload.si_value or ""
    bl_value = payload.bl_value or ""
    if len(si_value) > MAX_VALUE_LENGTH or len(bl_value) > MAX_VALUE_LENGTH:
        return _error(400, "value_too_long", f"Values must be {MAX_VALUE_LENGTH} characters or fewer.")

    si_norm = normalise(field, si_value)
    bl_norm = normalise(field, bl_value)
    if not can_learn(field, si_norm, bl_norm, False):
        return _error(
            400,
            "invalid_pair",
            "These two values must be non-blank and different from each other to learn a pair.",
        )

    pairs = _load_pairs(store, email)

    existing = _find(pairs, field, si_norm, bl_norm)
    if existing is not None:
        return {"pair": existing}

    if len(pairs) >= MAX_PAIRS_PER_ACCOUNT:
        return _error(400, "too_many_pairs", f"You can save at most {MAX_PAIRS_PER_ACCOUNT} pairs.")

    record = {
        "id": secrets.token_hex(8),
        "field": field,
        "a": si_norm,
        "b": bl_norm,
        "si_raw": si_value,
        "bl_raw": bl_value,
        "added_by": email,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "source": payload.source or "",
    }
    pairs.append(record)
    _save_pairs(store, email, pairs)
    return {"pair": record}


@router.delete("/api/equivalences/{pair_id}")
async def delete_equivalence(pair_id: str, request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = current_user(request)
    if email is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    pairs = _load_pairs(store, email)
    remaining = [record for record in pairs if record.get("id") != pair_id]
    if len(remaining) == len(pairs):
        return _error(404, "not_found", "No such pair.")

    _save_pairs(store, email, remaining)
    return {"ok": True}


__all__ = ["router", "lookup_for"]
