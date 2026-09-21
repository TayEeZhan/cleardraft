"""Tests for accounts: adapters/store.py's MemoryStore, api/_accounts.py's
signup/login/session/mailbox routes, and the accounts-aware behaviour of
POST /api/process-email in api/index.py.

The model tier is OFF for every test (tests/conftest.py sets
CLEARDRAFT_USE_MODEL=0). Storage is forced to adapters.store.MemoryStore for
every test in this file (CLEARDRAFT_STORE=memory) and any real Upstash env
vars are stripped first, so a developer's local .env can never make these
tests reach a real Redis - same hermetic-by-default discipline as the model
tier. The cached store instance is reset before and after every test so one
test's users/sessions never leak into the next.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from adapters import store as store_module
from api.index import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTACHMENTS = os.path.join(ROOT, "data", "attachments")
INBOX = os.path.join(ROOT, "data", "inbox")

_UPSTASH_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)


@pytest.fixture(autouse=True)
def memory_store(monkeypatch):
    for name in _UPSTASH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLEARDRAFT_STORE", "memory")
    store_module.reset_store_cache()
    yield
    store_module.reset_store_cache()


@pytest.fixture
def client():
    # A fresh client (and cookie jar) per test, so sessions never leak
    # between tests even though the store is also reset.
    return TestClient(app)


def _attachment(name: str) -> bytes:
    with open(os.path.join(ATTACHMENTS, name), "rb") as fh:
        return fh.read()


def _email_004_json() -> dict:
    with open(os.path.join(INBOX, "email_004.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _signup(client, email="alice@example.com", password="correct horse battery"):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


# ---------------------------------------------------------------------------
# adapters/store.py: MemoryStore
# ---------------------------------------------------------------------------
def test_memory_store_get_set_delete():
    store = store_module.MemoryStore()
    assert store.get("k") is None
    store.set("k", "v")
    assert store.get("k") == "v"
    store.delete("k")
    assert store.get("k") is None


def test_memory_store_expiry():
    store = store_module.MemoryStore()
    store.set("k", "v", ex_seconds=-1)  # already expired
    assert store.get("k") is None


def test_memory_store_incr():
    store = store_module.MemoryStore()
    assert store.incr("counter") == 1
    assert store.incr("counter") == 2
    assert store.incr("counter") == 3


def test_get_store_is_none_without_configuration(monkeypatch):
    for name in _UPSTASH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CLEARDRAFT_STORE", raising=False)
    store_module.reset_store_cache()
    assert store_module.get_store() is None
    store_module.reset_store_cache()


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------
def test_signup_sets_cookie_and_me_returns_user(client):
    resp = _signup(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "alice@example.com"
    assert "created" in body["user"]
    assert "cd_session" in resp.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "alice@example.com"


def test_signup_lowercases_and_strips_email(client):
    resp = client.post(
        "/api/auth/signup",
        json={"email": "  Alice@Example.com  ", "password": "correct horse battery"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "alice@example.com"


def test_duplicate_signup_is_409(client):
    _signup(client)
    resp = _signup(client)
    assert resp.status_code == 409
    assert resp.json()["error"] == "email_taken"


def test_weak_password_is_400(client):
    resp = client.post(
        "/api/auth/signup", json={"email": "bob@example.com", "password": "short"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "weak_password"


def test_bad_email_is_400(client):
    resp = client.post(
        "/api/auth/signup", json={"email": "not-an-email", "password": "correct horse battery"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_email"


def test_stored_user_record_has_no_plaintext_password(client):
    _signup(client, password="a very secret passphrase")
    store = store_module.get_store()
    raw = store.get("user:alice@example.com")
    assert raw is not None
    record = json.loads(raw)
    assert set(record.keys()) == {"email", "created", "salt", "hash"}
    dumped = json.dumps(record)
    assert "a very secret passphrase" not in dumped


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
def test_login_wrong_password_and_unknown_email_share_message(client):
    _signup(client, email="carol@example.com", password="correct horse battery")

    wrong = client.post(
        "/api/auth/login", json={"email": "carol@example.com", "password": "totally wrong"}
    )
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "totally wrong"}
    )

    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json()["error"] == "bad_credentials"
    assert unknown.json()["error"] == "bad_credentials"
    assert wrong.json()["detail"] == unknown.json()["detail"] == "Email or password is incorrect."


def test_login_correct_password_sets_cookie(client):
    _signup(client, email="dana@example.com", password="correct horse battery")
    client.post("/api/auth/logout")

    resp = client.post(
        "/api/auth/login",
        json={"email": "dana@example.com", "password": "correct horse battery"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "dana@example.com"
    assert "cd_session" in resp.cookies


def test_eleven_failed_logins_gives_429(client):
    _signup(client, email="erin@example.com", password="correct horse battery")
    client.post("/api/auth/logout")

    statuses = []
    for _ in range(11):
        resp = client.post(
            "/api/auth/login", json={"email": "erin@example.com", "password": "wrong password"}
        )
        statuses.append(resp.status_code)

    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429
    assert resp.json()["error"] == "too_many_attempts"


# ---------------------------------------------------------------------------
# Logout / me
# ---------------------------------------------------------------------------
def test_logout_invalidates_session(client):
    _signup(client, email="frank@example.com")
    assert client.get("/api/auth/me").status_code == 200

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    me = client.get("/api/auth/me")
    assert me.status_code == 401
    assert me.json()["error"] == "not_signed_in"


def test_me_without_session_is_401(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"] == "not_signed_in"


# ---------------------------------------------------------------------------
# Mail requires auth
# ---------------------------------------------------------------------------
def test_get_mail_requires_auth(client):
    resp = client.get("/api/mail")
    assert resp.status_code == 401


def test_delete_mail_requires_auth(client):
    resp = client.delete("/api/mail")
    assert resp.status_code == 401


def test_import_mail_requires_auth(client):
    resp = client.post("/api/mail/import", json={"board": [], "detail": {}})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# process-email + accounts integration
# ---------------------------------------------------------------------------
def _upload_email_004(client):
    with open(os.path.join(ROOT, "web", "samples", "eml", "01_mismatch_email_004.eml"), "rb") as fh:
        data = fh.read()
    return client.post(
        "/api/process-email",
        files={"eml": ("01_mismatch_email_004.eml", data, "message/rfc822")},
    )


def test_process_email_while_signed_in_is_saved_and_appears_in_mail(client):
    _signup(client, email="grace@example.com")

    resp = _upload_email_004(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["saved"] is True
    email_id = body["board"]["email_id"]

    mail = client.get("/api/mail")
    assert mail.status_code == 200
    mail_body = mail.json()
    ids = [row["email_id"] for row in mail_body["board"]]
    assert email_id in ids
    assert email_id in mail_body["detail"]


def test_process_email_while_signed_out_is_not_saved(client):
    resp = _upload_email_004(client)
    assert resp.status_code == 200
    assert resp.json()["saved"] is False
    assert "save_error" not in resp.json() or resp.json().get("save_error") is None


# ---------------------------------------------------------------------------
# Import: merge, dedupe, validation
# ---------------------------------------------------------------------------
def _row(email_id: str) -> dict:
    return {"email_id": email_id, "subject": f"subject for {email_id}"}


def test_import_merges_dedupes_and_rejects_bad_ids(client):
    _signup(client, email="henry@example.com")

    first = client.post(
        "/api/mail/import",
        json={
            "board": [_row("up_aaa"), _row("up_bbb")],
            "detail": {"up_aaa": {"x": 1}, "up_bbb": {"x": 2}},
        },
    )
    assert first.status_code == 200
    assert first.json()["count"] == 2

    # Second import: "up_aaa" reappears (dedupe should keep just one), plus
    # a genuinely new row and a row whose id does not start with "up_" (must
    # be rejected, not merged).
    second = client.post(
        "/api/mail/import",
        json={
            "board": [_row("up_aaa"), _row("up_ccc"), {"email_id": "not-a-real-id"}],
            "detail": {"up_aaa": {"x": 99}, "up_ccc": {"x": 3}},
        },
    )
    assert second.status_code == 200
    assert second.json()["count"] == 3  # aaa, bbb, ccc - not-a-real-id rejected

    mail = client.get("/api/mail").json()
    ids = {row["email_id"] for row in mail["board"]}
    assert ids == {"up_aaa", "up_bbb", "up_ccc"}
    assert "not-a-real-id" not in mail["detail"]


def test_import_too_large_is_413(client):
    _signup(client, email="ivy@example.com")
    huge_row = {"email_id": "up_huge", "subject": "x" * (3 * 1024 * 1024)}
    resp = client.post("/api/mail/import", json={"board": [huge_row], "detail": {}})
    assert resp.status_code == 413
    assert resp.json()["error"] == "too_large"


def test_import_caps_mailbox_at_200_dropping_oldest(client):
    _signup(client, email="jack@example.com")

    board = [_row(f"up_{i:03d}") for i in range(205)]
    detail = {row["email_id"]: {"i": i} for i, row in enumerate(board)}
    resp = client.post("/api/mail/import", json={"board": board, "detail": detail})
    assert resp.status_code == 200
    assert resp.json()["count"] == 200

    mail = client.get("/api/mail").json()
    ids = {row["email_id"] for row in mail["board"]}
    assert len(ids) == 200
    # Rows were given newest-first; the tail (oldest) must have been dropped.
    assert "up_000" in ids
    assert "up_204" not in ids


# ---------------------------------------------------------------------------
# Delete one / delete all
# ---------------------------------------------------------------------------
def test_delete_one_email_and_delete_all(client):
    _signup(client, email="karen@example.com")
    client.post(
        "/api/mail/import",
        json={
            "board": [_row("up_x"), _row("up_y")],
            "detail": {"up_x": {}, "up_y": {}},
        },
    )

    resp = client.delete("/api/mail/up_x")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    mail = client.get("/api/mail").json()
    ids = {row["email_id"] for row in mail["board"]}
    assert ids == {"up_y"}
    assert "up_x" not in mail["detail"]

    resp_all = client.delete("/api/mail")
    assert resp_all.status_code == 200
    assert resp_all.json() == {"ok": True}

    mail_after = client.get("/api/mail").json()
    assert mail_after == {"board": [], "detail": {}}


# ---------------------------------------------------------------------------
# Accounts unavailable (no store configured)
# ---------------------------------------------------------------------------
def test_accounts_unavailable_is_503(client, monkeypatch):
    for name in _UPSTASH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CLEARDRAFT_STORE", raising=False)
    store_module.reset_store_cache()
    try:
        resp = client.post(
            "/api/auth/signup", json={"email": "nobody@example.com", "password": "whatever12345"}
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "accounts_unavailable"

        me = client.get("/api/auth/me")
        assert me.status_code == 503

        mail = client.get("/api/mail")
        assert mail.status_code == 503
    finally:
        store_module.reset_store_cache()


def test_health_reports_accounts_configured(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["accounts"] is True


# ---------------------------------------------------------------------------
# "Paste an email" mode
# ---------------------------------------------------------------------------
def test_paste_mode_with_body_and_two_attachments_gives_mismatch(client):
    email_004 = _email_004_json()
    resp = client.post(
        "/api/process-email",
        data={"subject": email_004["subject"], "body": email_004["body"], "sender": email_004["from"]},
        files=[
            ("files", ("email_004_SI.txt", _attachment("email_004_SI.txt"), "text/plain")),
            ("files", ("email_004_BL.txt", _attachment("email_004_BL.txt"), "text/plain")),
        ],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["board"]["status"] == "MISMATCH"
    assert body["board"]["category"] == "BL_COMPARISON"
    assert body["board"]["defect_fields"] == ["consignee", "notify_party"]
    assert body["detail"]["filename"] == "Pasted email"
    assert body["board"]["email_id"].startswith("up_")


def test_paste_mode_with_no_body_and_no_eml_is_400_missing_email(client):
    resp = client.post("/api/process-email", data={"subject": "hello"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "missing_email"
