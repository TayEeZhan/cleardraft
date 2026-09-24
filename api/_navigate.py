"""POST /api/navigate - the AI fallback for the "Find anything" navigator.

OWNER: whoever built the navigator. A TRANSPORT LAYER ONLY, same discipline as
api/_equivalences.py and api/_dataset.py.

RULES FIRST, AI ONLY AS A FALLBACK: web/navigator.js matches a clerk's typed
query against a small, fixed destination index with plain deterministic string
scoring and only calls this endpoint when that local match is weak or empty.
Even then, the model can only ever pick ONE id from a fixed list this module
owns - it can never invent a destination. That fixed list is
web/navigator-destinations.json: the same file web/navigator.js fetches for
its own {id, title, hint, keywords} index, read here directly off disk (never
trusted from the request) so the two can never drift apart - see
tests/test_navigate_api.py for the id-parity check.

Included as a router from api/index.py, same pattern as api/_accounts.py,
api/_equivalences.py and api/_dataset.py - this file starts with `_` because
every non-underscore file in `api/` becomes its own Vercel function.

Routes:
    POST /api/navigate  - {"query": str} ->
        200 {"id": <one destination id> | null, "reason": str}
        400 query_too_long / empty_query
        503 model_unavailable (model tier off, no key, or the call failed)

Model budget: at most ONE attempt and 8 real seconds for this request - a
navigation aid must fail fast, never hang a clerk's search box. Enforced the
same way api/_dataset.py enforces its own request budget: a
[attempts_left, deadline] list set into adapters.model._BUDGET for the
duration of this call only.
"""
from __future__ import annotations

import json
import os
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import adapters.model as model_mod
from adapters.model import ModelUnavailable, available as model_available, complete_json

router = APIRouter()

#: A clerk's typed query, not a document - kept short on purpose. Checked
#: against the raw (unstripped) length, matching the acceptance criterion
#: "query <= 200 chars (else 400)" literally.
_MAX_QUERY_CHARS = 200

#: One attempt, eight real seconds - see module docstring.
_MODEL_ATTEMPT_BUDGET = 1
_MODEL_TIME_BUDGET_SECONDS = 8.0

#: Reason strings the model returns are shown straight to a clerk - keep them short.
_MAX_REASON_CHARS = 140

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESTINATIONS_PATH = os.path.join(_ROOT, "web", "navigator-destinations.json")


def _load_destinations() -> "list[dict]":
    with open(_DESTINATIONS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("navigator-destinations.json must be a JSON array")
    cleaned = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if not isinstance(entry.get("id"), str) or not entry["id"]:
            continue
        cleaned.append(entry)
    return cleaned


# Loaded once at import - read straight off disk, never from the request, so
# a caller can never smuggle in an id that isn't a real destination.
_DESTINATIONS = _load_destinations()
_VALID_IDS = {d["id"] for d in _DESTINATIONS}


class NavigateRequest(BaseModel):
    query: str


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


def _prompt(query: str) -> str:
    lines = [f'- {d["id"]}: {d["title"]} — {d["hint"]}' for d in _DESTINATIONS]
    return (
        "A shipping-desk clerk typed this into ClearDraft's \"Find anything\" "
        f"search box:\n\"{query}\"\n\n"
        "Pick the single best destination for what they want, from this FIXED "
        "list only (id: title — hint). Never invent an id that is not "
        "listed. If nothing genuinely fits, answer with a null id.\n\n"
        + "\n".join(lines)
    )


@router.post("/api/navigate")
async def navigate(payload: NavigateRequest):
    if len(payload.query) > _MAX_QUERY_CHARS:
        return _error(400, "query_too_long", f"Query must be {_MAX_QUERY_CHARS} characters or fewer.")

    query = payload.query.strip()
    if not query:
        return _error(400, "empty_query", "Query must not be empty.")

    if not model_available():
        return _error(503, "model_unavailable", "AI help is off right now.")

    budget = [_MODEL_ATTEMPT_BUDGET, time.monotonic() + _MODEL_TIME_BUDGET_SECONDS]
    token = model_mod._BUDGET.set(budget)
    try:
        result = complete_json(
            _prompt(query),
            schema_hint='{"id": "<one destination id from the list, or null>", "reason": "<short, plain-English, <=140 chars>"}',
            max_tokens=200,
        )
    except ModelUnavailable:
        return _error(503, "model_unavailable", "AI help is off right now.")
    finally:
        model_mod._BUDGET.reset(token)

    chosen = result.get("id") if isinstance(result, dict) else None
    reason = result.get("reason") if isinstance(result, dict) else None
    if not isinstance(reason, str):
        reason = ""

    if chosen not in _VALID_IDS:
        # The ONLY validation gate that matters: whatever the model answered,
        # an id that is not in our own fixed list can never reach the client
        # as a real destination - it is a plain null instead.
        chosen = None

    return {"id": chosen, "reason": reason[:_MAX_REASON_CHARS]}


__all__ = ["router"]
