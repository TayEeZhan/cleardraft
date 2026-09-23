"""Tests for learned equivalences: api/_equivalences.py's routes and their
effect on POST /api/check and POST /api/process-email in api/index.py.

Same hermetic setup as tests/test_accounts.py: storage is forced to
adapters.store.MemoryStore (CLEARDRAFT_STORE=memory) with any real Upstash
env vars stripped first, and the cached store instance is reset before and
after every test so nothing leaks between tests. The model tier is off
(tests/conftest.py).
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from adapters import store as store_module
from api.index import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTACHMENTS = os.path.join(ROOT, "data", "attachments")

_UPSTASH_ENV_VARS = (
    "KV_REST_API_URL",
    "KV_REST_API_TOKEN",
    "UPSTASH_REDIS_REST_URL",
    "UPSTASH_REDIS_REST_TOKEN",
)

# The email_004 SI/BL pair (see tests/test_api.py): a real planted mismatch
# on consignee and notify_party. SI names "EAST BRIGHT FZ-LLC"; the draft BL
# names "UAB NOVAKOPA" for both the consignee ("To the Order of") and the
# notify party.
CONSIGNEE_SI_VALUE = "EAST BRIGHT FZ-LLC"
CONSIGNEE_BL_VALUE = "UAB NOVAKOPA"


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
    return TestClient(app)


def _attachment(name: str) -> bytes:
    with open(os.path.join(ATTACHMENTS, name), "rb") as fh:
        return fh.read()


def _signup(client, email="alice@example.com", password="correct horse battery"):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def _check_email_004(client):
    files = {
        "si": ("email_004_SI.txt", _attachment("email_004_SI.txt"), "text/plain"),
        "bl": ("email_004_BL.txt", _attachment("email_004_BL.txt"), "text/plain"),
    }
    return client.post("/api/check", files=files)


def _learn_consignee_pair(client, **overrides):
    payload = {
        "field": "consignee",
        "si_value": CONSIGNEE_SI_VALUE,
        "bl_value": CONSIGNEE_BL_VALUE,
        "source": "web-check",
    }
    payload.update(overrides)
    return client.post("/api/equivalences", json=payload)


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------
def test_post_while_signed_out_is_401(client):
    resp = _learn_consignee_pair(client)
    assert resp.status_code == 401
    assert resp.json()["error"] == "not_signed_in"


def test_get_while_signed_out_is_401(client):
    resp = client.get("/api/equivalences")
    assert resp.status_code == 401


def test_delete_while_signed_out_is_401(client):
    resp = client.delete("/api/equivalences/whatever")
    assert resp.status_code == 401


def test_accounts_unavailable_is_503(client, monkeypatch):
    for name in _UPSTASH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("CLEARDRAFT_STORE", raising=False)
    store_module.reset_store_cache()
    try:
        resp = _learn_consignee_pair(client)
        assert resp.status_code == 503
        assert resp.json()["error"] == "accounts_unavailable"
    finally:
        store_module.reset_store_cache()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_numeric_field_is_400(client):
    _signup(client)
    resp = _learn_consignee_pair(client, field="gross_weight_kg", si_value="131058", bl_value="132058")
    assert resp.status_code == 400
    assert resp.json()["error"] == "disallowed_field"


def test_unknown_field_is_400(client):
    _signup(client)
    resp = _learn_consignee_pair(client, field="bogus")
    assert resp.status_code == 400
    assert resp.json()["error"] == "disallowed_field"


def test_blank_value_is_400(client):
    _signup(client)
    resp = _learn_consignee_pair(client, bl_value="")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_pair"


def test_already_equal_values_are_400(client):
    _signup(client)
    resp = _learn_consignee_pair(client, bl_value=CONSIGNEE_SI_VALUE)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_pair"


def test_oversized_value_is_400(client):
    _signup(client)
    resp = _learn_consignee_pair(client, si_value="x" * 201)
    assert resp.status_code == 400
    assert resp.json()["error"] == "value_too_long"


def test_exactly_200_chars_is_accepted(client):
    _signup(client)
    resp = _learn_consignee_pair(client, si_value="x" * 200, bl_value="y" * 200)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Learn, list, duplicate, undo
# ---------------------------------------------------------------------------
def test_learn_then_list_shows_the_pair(client):
    _signup(client)
    learn = _learn_consignee_pair(client)
    assert learn.status_code == 200
    pair = learn.json()["pair"]
    assert pair["field"] == "consignee"
    assert pair["a"] and pair["b"]
    assert pair["added_by"] == "alice@example.com"
    assert pair["added_at"]
    assert pair["si_raw"] == CONSIGNEE_SI_VALUE
    assert pair["bl_raw"] == CONSIGNEE_BL_VALUE
    assert pair["source"] == "web-check"

    listing = client.get("/api/equivalences")
    assert listing.status_code == 200
    ids = [p["id"] for p in listing.json()["pairs"]]
    assert pair["id"] in ids


def test_duplicate_learn_is_idempotent_and_returns_existing(client):
    _signup(client)
    first = _learn_consignee_pair(client)
    second = _learn_consignee_pair(client)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["pair"]["id"] == second.json()["pair"]["id"]

    listing = client.get("/api/equivalences").json()
    assert len(listing["pairs"]) == 1


def test_reversed_order_is_the_same_duplicate_pair(client):
    _signup(client)
    first = _learn_consignee_pair(client)
    reversed_ = _learn_consignee_pair(client, si_value=CONSIGNEE_BL_VALUE, bl_value=CONSIGNEE_SI_VALUE)
    assert reversed_.status_code == 200
    assert reversed_.json()["pair"]["id"] == first.json()["pair"]["id"]

    listing = client.get("/api/equivalences").json()
    assert len(listing["pairs"]) == 1


def test_delete_unknown_id_is_404(client):
    _signup(client)
    resp = client.delete("/api/equivalences/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"] == "not_found"


def test_delete_removes_the_pair(client):
    _signup(client)
    pair = _learn_consignee_pair(client).json()["pair"]

    resp = client.delete(f"/api/equivalences/{pair['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    listing = client.get("/api/equivalences").json()
    assert listing["pairs"] == []


# ---------------------------------------------------------------------------
# End-to-end: learn -> next check clears the field -> undo -> mismatch returns
# ---------------------------------------------------------------------------
def test_signed_out_check_is_unaffected_by_a_learned_pair(client):
    """A pair on someone's account must never leak into a signed-out check."""
    _signup(client, email="alice@example.com")
    _learn_consignee_pair(client)
    client.post("/api/auth/logout")

    resp = _check_email_004(client)
    body = resp.json()
    assert body["defect_fields"] == ["consignee", "notify_party"]


def test_learned_pair_clears_the_field_on_the_next_check(client):
    _signup(client, email="alice@example.com")

    before = _check_email_004(client)
    assert before.json()["defect_fields"] == ["consignee", "notify_party"]

    learn = _learn_consignee_pair(client)
    assert learn.status_code == 200

    after = _check_email_004(client)
    body = after.json()
    assert body["defect_fields"] == ["notify_party"]

    consignee_row = next(r for r in body["comparisons"] if r["field"] == "consignee")
    assert consignee_row["matched"] is True
    assert consignee_row["learned"] is True

    notify_row = next(r for r in body["comparisons"] if r["field"] == "notify_party")
    assert notify_row["matched"] is False
    assert notify_row["learned"] is False


def test_a_different_signed_in_user_still_sees_the_mismatch(client):
    """Per-account isolation: one clerk's learned pair must not affect another."""
    _signup(client, email="alice@example.com")
    _learn_consignee_pair(client)
    client.post("/api/auth/logout")

    other_client = TestClient(app)
    other_client.post(
        "/api/auth/signup", json={"email": "bob@example.com", "password": "correct horse battery"}
    )
    resp = _check_email_004(other_client)
    assert resp.json()["defect_fields"] == ["consignee", "notify_party"]


def test_undo_brings_the_mismatch_back(client):
    _signup(client, email="alice@example.com")
    pair = _learn_consignee_pair(client).json()["pair"]

    cleared = _check_email_004(client)
    assert cleared.json()["defect_fields"] == ["notify_party"]

    delete_resp = client.delete(f"/api/equivalences/{pair['id']}")
    assert delete_resp.status_code == 200

    after_undo = _check_email_004(client)
    assert after_undo.json()["defect_fields"] == ["consignee", "notify_party"]


def test_learned_pair_also_clears_process_email_path(client):
    """The same known_equal must apply on the /api/process-email path
    (_process_eml_bytes), not just /api/check."""
    _signup(client, email="alice@example.com")
    _learn_consignee_pair(client)

    with open(os.path.join(ROOT, "web", "samples", "eml", "01_mismatch_email_004.eml"), "rb") as fh:
        data = fh.read()
    resp = client.post(
        "/api/process-email",
        files={"eml": ("01_mismatch_email_004.eml", data, "message/rfc822")},
    )
    assert resp.status_code == 200
    board = resp.json()["board"]
    assert "consignee" not in board["defect_fields"]
    assert "notify_party" in board["defect_fields"]
