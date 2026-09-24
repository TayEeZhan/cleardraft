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
    MAX_INPUT_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_PAIRS_PER_ACCOUNT,
    MAX_VALUE_LENGTH,
    build_pair_index,
    can_learn,
    evaluate_case,
    make_lookup,
    pair_key,
)
from core.normalise import normalise
from core.reply import draft_reply, draft_recheck_reply
from core.recheck import RecheckRow
from core.types import Decision, Email, FieldComparison, FieldValue

router = APIRouter()

#: Caps for POST /api/equivalences/evaluate. Kept generous but bounded: this
#: endpoint may be called with a whole inbox board, but it must never accept
#: an unbounded body.
MAX_EVALUATE_CASES = 600
MAX_ROWS_PER_CASE = 7
MAX_EVALUATE_BODY_CHARS = 20000


class LearnRequest(BaseModel):
    field: str
    si_value: str
    bl_value: str
    source: str = ""
    note: str = ""


class PatchRequest(BaseModel):
    si_value: "str | None" = None
    bl_value: "str | None" = None
    note: "str | None" = None
    field: "str | None" = None


class EvaluateRequest(BaseModel):
    cases: "list[dict]" = []
    draft: bool = False


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

    pairs = _load_pairs(store, email)
    for record in pairs:
        record.setdefault("note", "")
        record.setdefault("updated_at", None)
    return {"pairs": pairs}


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
    if len(si_value) > MAX_INPUT_LENGTH or len(bl_value) > MAX_INPUT_LENGTH:
        return _error(400, "value_too_long", f"Values must be {MAX_INPUT_LENGTH} characters or fewer.")

    note = payload.note or ""
    if len(note) > MAX_NOTE_LENGTH:
        return _error(400, "note_too_long", f"Notes must be {MAX_NOTE_LENGTH} characters or fewer.")

    si_norm = normalise(field, si_value)
    bl_norm = normalise(field, bl_value)
    if not can_learn(field, si_norm, bl_norm, False):
        return _error(
            400,
            "invalid_pair",
            "These two values must be non-blank and different from each other to learn a pair.",
        )
    if len(si_norm) > MAX_VALUE_LENGTH or len(bl_norm) > MAX_VALUE_LENGTH:
        return _error(400, "value_too_long", f"Values must normalise to {MAX_VALUE_LENGTH} characters or fewer.")

    pairs = _load_pairs(store, email)

    existing = _find(pairs, field, si_norm, bl_norm)
    if existing is not None:
        existing.setdefault("note", "")
        existing.setdefault("updated_at", None)
        if not existing.get("note") and note:
            existing["note"] = note
            existing["updated_at"] = datetime.now(timezone.utc).isoformat()
            _save_pairs(store, email, pairs)
        return {"pair": existing, "already_marked": True}

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
        "updated_at": None,
        "source": payload.source or "",
        "note": note,
    }
    pairs.append(record)
    _save_pairs(store, email, pairs)
    return {"pair": record}


@router.patch("/api/equivalences/{pair_id}")
async def edit_equivalence(pair_id: str, payload: PatchRequest, request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = current_user(request)
    if email is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    pairs = _load_pairs(store, email)
    record = next((r for r in pairs if isinstance(r, dict) and r.get("id") == pair_id), None)
    if record is None:
        return _error(404, "not_found", "No such pair.")

    if payload.field is not None and payload.field != record.get("field"):
        return _error(400, "field_immutable", "A pair's field cannot be changed. Remove and re-add instead.")

    if payload.note is not None:
        if len(payload.note) > MAX_NOTE_LENGTH:
            return _error(400, "note_too_long", f"Notes must be {MAX_NOTE_LENGTH} characters or fewer.")

    field = record.get("field")
    wording_changed = payload.si_value is not None or payload.bl_value is not None
    new_a, new_b = record.get("a"), record.get("b")
    new_si_raw = record.get("si_raw", "")
    new_bl_raw = record.get("bl_raw", "")

    if wording_changed:
        si_value = payload.si_value if payload.si_value is not None else record.get("si_raw", "")
        bl_value = payload.bl_value if payload.bl_value is not None else record.get("bl_raw", "")
        if len(si_value) > MAX_INPUT_LENGTH or len(bl_value) > MAX_INPUT_LENGTH:
            return _error(400, "value_too_long", f"Values must be {MAX_INPUT_LENGTH} characters or fewer.")

        si_norm = normalise(field, si_value)
        bl_norm = normalise(field, bl_value)
        if not can_learn(field, si_norm, bl_norm, False):
            return _error(
                400,
                "invalid_pair",
                "These two values must be non-blank and different from each other to learn a pair.",
            )
        if len(si_norm) > MAX_VALUE_LENGTH or len(bl_norm) > MAX_VALUE_LENGTH:
            return _error(400, "value_too_long", f"Values must normalise to {MAX_VALUE_LENGTH} characters or fewer.")

        key = pair_key(field, si_norm, bl_norm)
        for other in pairs:
            if not isinstance(other, dict) or other.get("id") == pair_id:
                continue
            if other.get("field") != field:
                continue
            oa, ob = other.get("a"), other.get("b")
            if not isinstance(oa, str) or not isinstance(ob, str):
                continue
            if pair_key(field, oa, ob) == key:
                return _error(409, "duplicate_pair", "Another saved pair already has this exact wording.")

        new_a, new_b = si_norm, bl_norm
        new_si_raw, new_bl_raw = si_value, bl_value

    record["a"] = new_a
    record["b"] = new_b
    record["si_raw"] = new_si_raw
    record["bl_raw"] = new_bl_raw
    if payload.note is not None:
        record["note"] = payload.note
    record.setdefault("note", "")
    record["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_pairs(store, email, pairs)
    return {"pair": record}


# ---------------------------------------------------------------------------
# Evaluate: apply "the one rule" to saved/checked cases, live, per account
# ---------------------------------------------------------------------------
def _fv_from(obj) -> "FieldValue | None":
    if not isinstance(obj, dict):
        return None
    value = obj.get("value")
    if not isinstance(value, str):
        return None
    return FieldValue(
        value=value,
        raw=obj.get("raw") if isinstance(obj.get("raw"), str) else value,
        line_no=obj.get("line_no") if isinstance(obj.get("line_no"), int) else 0,
        label=obj.get("label") if isinstance(obj.get("label"), str) else "",
        decided_by=obj.get("decided_by") if obj.get("decided_by") in ("rule", "model") else "rule",
    )


def _build_reply_drafts(case: dict, evaluation: dict) -> "tuple[str | None, str | None]":
    """Rebuild Email/Decision (and RecheckRow, if present) from the JSON case
    and redraft with the real templates. Any reconstruction problem simply
    omits the draft - never a 500."""
    reply_draft = None
    recheck_reply_draft = None
    try:
        email = Email(
            email_id=str(case.get("email_id", "")),
            sender=str(case.get("from", "")),
            subject=str(case.get("subject", "")),
            body=str(case.get("body", "")),
        )
    except Exception:
        return None, None

    try:
        comparisons = []
        for row in case.get("comparisons") or []:
            if not isinstance(row, dict):
                continue
            field = row.get("field")
            if not isinstance(field, str):
                continue
            si_fv = _fv_from(row.get("si"))
            bl_fv = _fv_from(row.get("bl"))
            matched = bool(row.get("matched", False)) or field in evaluation.get("rows", {})
            comparisons.append(
                FieldComparison(
                    field=field,
                    si=si_fv,
                    bl=bl_fv,
                    si_norm=None,
                    bl_norm=None,
                    matched=matched,
                    undecidable=bool(row.get("undecidable", False)),
                )
            )
        decision = Decision(
            email_id=str(case.get("email_id", "")),
            category=case.get("category", "BL_COMPARISON"),
            status=evaluation.get("status"),
            review_reason=case.get("review_reason"),
            has_defect=bool(evaluation.get("has_defect", False)),
            defect_fields=tuple(evaluation.get("defect_fields", ())),
            decided_by=case.get("decided_by", "rule"),
            comparisons=tuple(comparisons),
        )
        reply_draft = draft_reply(email, decision)
    except Exception:
        reply_draft = None

    recheck_obj = case.get("recheck")
    if isinstance(recheck_obj, dict) and isinstance(recheck_obj.get("rows"), list):
        try:
            recheck_effective = evaluation.get("recheck", {})
            rows = []
            for row in recheck_obj["rows"]:
                if not isinstance(row, dict):
                    continue
                field = row.get("field")
                if not isinstance(field, str):
                    continue
                si_str = row.get("si") if isinstance(row.get("si"), str) else ""
                v1_str = row.get("v1") if isinstance(row.get("v1"), str) else ""
                v2_str = row.get("v2") if isinstance(row.get("v2"), str) else ""
                outcome = recheck_effective.get(field, {}).get("outcome") if field in recheck_effective else row.get("outcome")
                v1_comparison = FieldComparison(
                    field=field,
                    si=FieldValue(value=si_str, raw=si_str, line_no=0, label=""),
                    bl=FieldValue(value=v1_str, raw=v1_str, line_no=0, label=""),
                    si_norm=None,
                    bl_norm=None,
                    matched=(row.get("outcome") in ("ok", "fixed")),
                )
                v2_comparison = FieldComparison(
                    field=field,
                    si=FieldValue(value=si_str, raw=si_str, line_no=0, label=""),
                    bl=FieldValue(value=v2_str, raw=v2_str, line_no=0, label=""),
                    si_norm=None,
                    bl_norm=None,
                    matched=(outcome in ("ok", "fixed")),
                )
                rows.append(
                    RecheckRow(field=field, outcome=outcome or row.get("outcome"), v1=v1_comparison, v2=v2_comparison)
                )
            recheck_reply_draft = draft_recheck_reply(email, rows)
        except Exception:
            recheck_reply_draft = None

    return reply_draft, recheck_reply_draft


@router.post("/api/equivalences/evaluate")
async def evaluate_equivalences(payload: EvaluateRequest, request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = current_user(request)
    if email is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    cases = payload.cases
    if len(cases) > MAX_EVALUATE_CASES:
        return _error(400, "too_many_cases", f"At most {MAX_EVALUATE_CASES} cases per request.")
    if payload.draft and len(cases) != 1:
        return _error(400, "draft_requires_one_case", "draft=true requires exactly one case.")

    try:
        body_chars = len(json.dumps(cases))
    except Exception:
        body_chars = MAX_EVALUATE_BODY_CHARS + 1
    if body_chars > MAX_EVALUATE_BODY_CHARS:
        return _error(400, "body_too_large", f"Request body must be {MAX_EVALUATE_BODY_CHARS} characters or fewer.")

    for case in cases:
        if not isinstance(case, dict):
            return _error(400, "invalid_case", "Each case must be an object.")
        comparisons = case.get("comparisons") or []
        if isinstance(comparisons, list) and len(comparisons) > MAX_ROWS_PER_CASE:
            return _error(400, "too_many_rows", f"At most {MAX_ROWS_PER_CASE} comparison rows per case.")
        recheck_obj = case.get("recheck")
        if isinstance(recheck_obj, dict):
            rrows = recheck_obj.get("rows") or []
            if isinstance(rrows, list) and len(rrows) > MAX_ROWS_PER_CASE:
                return _error(400, "too_many_rows", f"At most {MAX_ROWS_PER_CASE} recheck rows per case.")
        for row in list(comparisons if isinstance(comparisons, list) else []):
            if not isinstance(row, dict):
                continue
            for side in ("si", "bl"):
                obj = row.get(side)
                if isinstance(obj, dict):
                    value = obj.get("value")
                    if isinstance(value, str) and len(value) > MAX_INPUT_LENGTH:
                        return _error(400, "value_too_long", f"Values must be {MAX_INPUT_LENGTH} characters or fewer.")
        body_val = case.get("body")
        if isinstance(body_val, str) and len(body_val) > 20000:
            return _error(400, "body_too_long", "Case body must be 20,000 characters or fewer.")

    pairs = _load_pairs(store, email)
    pair_index = build_pair_index(pairs)

    results = []
    for case in cases:
        try:
            evaluation = evaluate_case(case, pair_index)
        except Exception:
            evaluation = {
                "email_id": case.get("email_id") if isinstance(case, dict) else "",
                "status": case.get("status") if isinstance(case, dict) else None,
                "defect_fields": list(case.get("defect_fields") or []) if isinstance(case, dict) else [],
                "has_defect": bool(case.get("has_defect", False)) if isinstance(case, dict) else False,
                "changed": False,
                "rows": {},
                "recheck": {},
            }
        entry = dict(evaluation)
        if payload.draft and evaluation.get("changed"):
            try:
                reply_draft, recheck_reply_draft = _build_reply_drafts(case, evaluation)
            except Exception:
                reply_draft, recheck_reply_draft = None, None
            if reply_draft is not None:
                entry["reply_draft"] = reply_draft
            if recheck_reply_draft is not None:
                entry["recheck_reply_draft"] = recheck_reply_draft
        results.append(entry)

    return {"cases": results}


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
