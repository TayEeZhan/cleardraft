"""Accounts: signup/login/session and a per-user saved mailbox.

OWNER: whoever built the accounts backend. A TRANSPORT + STORAGE layer only -
no pipeline logic lives here (that stays in core/, unchanged). This module
owns everything under /api/auth/* and /api/mail*, and exposes `current_user`
for api/index.py to import so /api/process-email can save a signed-in user's
result without this module importing anything from index.py (avoids a
circular import).

Storage: adapters/store.py's Store protocol (Upstash Redis in production,
an in-memory dict in tests/local - see get_store()). When no store is
configured, every route here answers 503 accounts_unavailable rather than
silently pretending accounts work.

Passwords: hashlib.scrypt (n=2**14, r=8, p=1, dklen=32) with a random 16-byte
salt per user, compared with hmac.compare_digest. Never stored or logged in
the clear. A login for an unknown email still runs one scrypt call (against a
fixed dummy salt) so the response time does not reveal whether the address
has an account.

Sessions: a random 32-byte URL-safe token lives ONLY in the client's cookie;
the server stores sha256(token) -> email, so a leaked store dump never
yields a usable session token.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from adapters.store import Store, StoreError, get_store

router = APIRouter()

#: Cookie that carries the session token. HttpOnly so page script can never
#: read it; SameSite=Lax so a plain cross-site GET can't ride it either.
SESSION_COOKIE = "cd_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days

#: scrypt parameters. n=2**14 keeps a single login under ~100ms on ordinary
#: hardware while still being far too slow to brute-force at scale.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
#: Fixed salt used ONLY to burn the same CPU time for an unknown email as a
#: real lookup would take. Never used for a real user's password.
_DUMMY_SALT = bytes(16)

#: Failed-login lockout: 10 failures per email inside a 15-minute window.
LOGIN_FAIL_LIMIT = 10
LOGIN_FAIL_WINDOW_SECONDS = 15 * 60

#: A saved mailbox is capped at 200 emails; the oldest are dropped to make
#: room for new ones (see merge_mailbox).
MAX_MAILBOX = 200
#: POST /api/mail/import is rejected outright above this many raw bytes.
MAX_IMPORT_BYTES = 2 * 1024 * 1024


class SignupRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


# ---------------------------------------------------------------------------
# Email / password validation
# ---------------------------------------------------------------------------
def _normalize_email(raw: str) -> str:
    return (raw or "").strip().lower()


def _valid_email(email: str) -> bool:
    """Basic format check: one "@", a non-empty local/domain, a dot in the
    domain, and a sane overall length. Not RFC 5322 - just enough to catch
    typos without rejecting real addresses."""
    if not email or len(email) > 254:
        return False
    if email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    if not local or not domain:
        return False
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        return False
    return True


def _valid_password(password: str) -> bool:
    return 8 <= len(password) <= 128


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, bytes.fromhex(hash_hex))


def _burn_dummy_hash(password: str) -> None:
    """Run one scrypt call for an unknown email, same cost as a real lookup."""
    _hash_password(password, _DUMMY_SALT)


# ---------------------------------------------------------------------------
# User records: user:<email> -> {"email", "created", "salt", "hash"}
# ---------------------------------------------------------------------------
def _user_key(email: str) -> str:
    return f"user:{email}"


def _get_user(store: Store, email: str) -> "dict[str, Any] | None":
    raw = store.get(_user_key(email))
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def _public_user(record: "dict[str, Any]") -> dict:
    """Never include salt/hash in an API response."""
    return {"email": record["email"], "created": record["created"]}


# ---------------------------------------------------------------------------
# Sessions: session:<sha256(token)> -> email
# ---------------------------------------------------------------------------
def _session_key(token: str) -> str:
    return "session:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "")
    return proto.split(",")[0].strip().lower() == "https"


def _set_session_cookie(resp: JSONResponse, store: Store, email: str, request: Request) -> None:
    token = secrets.token_urlsafe(32)
    store.set(_session_key(token), email, ex_seconds=SESSION_TTL_SECONDS)
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
        path="/",
    )


def current_user(request: Request) -> "str | None":
    """The signed-in user's email, or None. Safe to call with no store
    configured (returns None) so callers never need their own store check
    just to ask "is someone signed in"."""
    store = get_store()
    if store is None:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    try:
        return store.get(_session_key(token))
    except StoreError:
        # Store down: behave as signed out so uploads keep working.
        return None


# ---------------------------------------------------------------------------
# Mailbox: mail:<email> -> {"board": [...], "detail": {...}}
# ---------------------------------------------------------------------------
def _mail_key(email: str) -> str:
    return f"mail:{email}"


def load_mailbox(store: Store, email: str) -> dict:
    raw = store.get(_mail_key(email))
    if not raw:
        return {"board": [], "detail": {}}
    try:
        data = json.loads(raw)
    except ValueError:
        return {"board": [], "detail": {}}
    board = data.get("board") if isinstance(data, dict) else None
    detail = data.get("detail") if isinstance(data, dict) else None
    return {
        "board": board if isinstance(board, list) else [],
        "detail": detail if isinstance(detail, dict) else {},
    }


def save_mailbox(store: Store, email: str, mailbox: dict) -> None:
    store.set(_mail_key(email), json.dumps(mailbox))


def merge_mailbox(existing: dict, incoming_board: "list[dict]", incoming_detail: "dict[str, dict]") -> dict:
    """Merge incoming board/detail rows into an existing mailbox.

    Dedupe by email_id: an incoming row wins over an existing one with the
    same id (it is the newer result). The merged board is newest-first
    (incoming rows first, in the order given, then the remaining existing
    rows in their prior order) and capped at MAX_MAILBOX - once full, the
    oldest rows (the tail) are dropped to make room, and their detail
    entries are dropped with them.
    """
    seen: "set[str]" = set()
    merged_board: "list[dict]" = []
    merged_detail: "dict[str, dict]" = dict(existing.get("detail", {}))

    for item in incoming_board:
        email_id = item.get("email_id")
        if email_id in seen:
            continue
        seen.add(email_id)
        merged_board.append(item)
        if email_id in incoming_detail:
            merged_detail[email_id] = incoming_detail[email_id]

    for item in existing.get("board", []):
        email_id = item.get("email_id")
        if email_id in seen:
            continue
        seen.add(email_id)
        merged_board.append(item)

    merged_board = merged_board[:MAX_MAILBOX]
    kept_ids = {item.get("email_id") for item in merged_board}
    merged_detail = {k: v for k, v in merged_detail.items() if k in kept_ids}
    return {"board": merged_board, "detail": merged_detail}


def save_result_to_mailbox(store: Store, email: str, board_row: dict, detail_row: dict) -> int:
    """Save one process-email result (a single board row + its detail) into
    a user's mailbox, using the same dedupe/cap rule as bulk import. Returns
    the mailbox size after saving."""
    existing = load_mailbox(store, email)
    merged = merge_mailbox(existing, [board_row], {board_row["email_id"]: detail_row})
    save_mailbox(store, email, merged)
    return len(merged["board"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/api/auth/signup")
async def signup(payload: SignupRequest, request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = _normalize_email(payload.email)
    if not _valid_email(email):
        return _error(400, "invalid_email", "Enter a valid email address.")
    if not _valid_password(payload.password):
        return _error(400, "weak_password", "Password must be 8-128 characters.")

    if _get_user(store, email) is not None:
        return _error(409, "email_taken", "An account with this email already exists.")

    salt = secrets.token_bytes(16)
    hashed = _hash_password(payload.password, salt)
    created = datetime.now(timezone.utc).date().isoformat()
    record = {"email": email, "created": created, "salt": salt.hex(), "hash": hashed.hex()}
    store.set(_user_key(email), json.dumps(record))

    resp = JSONResponse(status_code=200, content={"user": _public_user(record)})
    _set_session_cookie(resp, store, email, request)
    return resp


@router.post("/api/auth/login")
async def login(payload: LoginRequest, request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = _normalize_email(payload.email)
    fail_key = f"loginfail:{email}"

    current_fails = store.get(fail_key)
    if current_fails is not None and int(current_fails) >= LOGIN_FAIL_LIMIT:
        return _error(
            429,
            "too_many_attempts",
            "Too many failed login attempts. Try again in a few minutes.",
        )

    record = _get_user(store, email)
    if record is None:
        _burn_dummy_hash(payload.password)
        ok = False
    else:
        ok = _verify_password(payload.password, record["salt"], record["hash"])

    if not ok:
        store.incr(fail_key, ex_seconds=LOGIN_FAIL_WINDOW_SECONDS)
        return _error(401, "bad_credentials", "Email or password is incorrect.")

    store.delete(fail_key)
    resp = JSONResponse(status_code=200, content={"user": _public_user(record)})
    _set_session_cookie(resp, store, email, request)
    return resp


@router.post("/api/auth/logout")
async def logout(request: Request):
    store = get_store()
    resp = JSONResponse(status_code=200, content={"ok": True})
    if store is not None:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            store.delete(_session_key(token))
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/api/auth/me")
async def me(request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = current_user(request)
    if email is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    record = _get_user(store, email)
    if record is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    return {"user": _public_user(record)}


@router.get("/api/mail")
async def get_mail(request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = current_user(request)
    if email is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    mailbox = load_mailbox(store, email)
    return {"board": mailbox["board"], "detail": mailbox["detail"]}


@router.post("/api/mail/import")
async def import_mail(request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = current_user(request)
    if email is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    raw_body = await request.body()
    if len(raw_body) > MAX_IMPORT_BYTES:
        return _error(413, "too_large", "Import payload exceeds 2 MB.")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        payload = None

    board_in = payload.get("board") if isinstance(payload, dict) else None
    detail_in = payload.get("detail") if isinstance(payload, dict) else None
    if not isinstance(board_in, list):
        board_in = []
    if not isinstance(detail_in, dict):
        detail_in = {}

    # Validate: only rows shaped like a real board entry survive - a dict
    # with a str email_id that starts "up_" (the prefix every uploaded/
    # pasted result gets - see api/index.py). Anything else is silently
    # dropped rather than failing the whole import over one bad row.
    valid_board = [
        item
        for item in board_in
        if isinstance(item, dict)
        and isinstance(item.get("email_id"), str)
        and item["email_id"].startswith("up_")
    ]
    valid_ids = {item["email_id"] for item in valid_board}
    valid_detail = {
        email_id: value
        for email_id, value in detail_in.items()
        if email_id in valid_ids and isinstance(value, dict)
    }

    existing = load_mailbox(store, email)
    merged = merge_mailbox(existing, valid_board, valid_detail)
    save_mailbox(store, email, merged)
    return {"count": len(merged["board"])}


@router.delete("/api/mail")
async def clear_mail(request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    email = current_user(request)
    if email is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    save_mailbox(store, email, {"board": [], "detail": {}})
    return {"ok": True}


@router.delete("/api/mail/{email_id}")
async def delete_mail_item(email_id: str, request: Request):
    store = get_store()
    if store is None:
        return _error(503, "accounts_unavailable", "Accounts are not configured on this deployment.")

    user_email = current_user(request)
    if user_email is None:
        return _error(401, "not_signed_in", "Sign in to continue.")

    mailbox = load_mailbox(store, user_email)
    mailbox["board"] = [item for item in mailbox["board"] if item.get("email_id") != email_id]
    mailbox["detail"].pop(email_id, None)
    save_mailbox(store, user_email, mailbox)
    return {"ok": True}


__all__ = ["router", "current_user", "save_result_to_mailbox", "MAX_MAILBOX", "MAX_IMPORT_BYTES"]
