"""Tests for POST /api/navigate - the AI fallback for the "Find anything"
navigator (api/_navigate.py).

Hermetic, same discipline as tests/test_model_budget.py: the model tier is
off by default (tests/conftest.py), and tests that need it "on" flip
CLEARDRAFT_USE_MODEL/ANTHROPIC_API_KEY back on with a fake key, then
monkeypatch api._navigate.complete_json directly - no real network call is
ever made here.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

import api._navigate as navigate_module
from api.index import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESTINATIONS_PATH = os.path.join(ROOT, "web", "navigator-destinations.json")


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def model_on(monkeypatch):
    monkeypatch.setenv("CLEARDRAFT_USE_MODEL", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    yield


def _post(client, query):
    return client.post("/api/navigate", json={"query": query})


# ---------------------------------------------------------------------------
# Validation - never reaches the model
# ---------------------------------------------------------------------------
def test_query_too_long_is_400(client):
    resp = _post(client, "x" * 201)
    assert resp.status_code == 400
    assert resp.json()["error"] == "query_too_long"


def test_query_at_the_cap_is_not_rejected_for_length(client):
    # 200 chars, model off (default) -> the length check passes and the
    # request proceeds to the model-availability check, which is what
    # actually answers 503 here - proving length alone isn't the blocker.
    resp = _post(client, "x" * 200)
    assert resp.status_code == 503


def test_empty_query_is_400(client):
    resp = _post(client, "   ")
    assert resp.status_code == 400
    assert resp.json()["error"] == "empty_query"


# ---------------------------------------------------------------------------
# Model off / unavailable -> 503
# ---------------------------------------------------------------------------
def test_model_off_is_503(client):
    # tests/conftest.py already sets CLEARDRAFT_USE_MODEL=0 for the whole suite.
    resp = _post(client, "download report")
    assert resp.status_code == 503
    assert resp.json()["error"] == "model_unavailable"


def test_model_available_but_call_fails_is_503(client, model_on, monkeypatch):
    def _boom(*args, **kwargs):
        raise navigate_module.ModelUnavailable("simulated failure")

    monkeypatch.setattr(navigate_module, "complete_json", _boom)
    resp = _post(client, "download report")
    assert resp.status_code == 503
    assert resp.json()["error"] == "model_unavailable"


# ---------------------------------------------------------------------------
# A valid id from the model -> 200, id passed through
# ---------------------------------------------------------------------------
def test_valid_id_is_returned(client, model_on, monkeypatch):
    monkeypatch.setattr(
        navigate_module,
        "complete_json",
        lambda *a, **k: {"id": "export-discrepancy", "reason": "sounds like a report download"},
    )
    resp = _post(client, "I want the report of what's wrong")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "export-discrepancy"
    assert body["reason"]


# ---------------------------------------------------------------------------
# An invented id -> {"id": null} - the one validation gate that matters
# ---------------------------------------------------------------------------
def test_invented_id_becomes_null(client, model_on, monkeypatch):
    monkeypatch.setattr(
        navigate_module,
        "complete_json",
        lambda *a, **k: {"id": "delete-everything", "reason": "not a real destination"},
    )
    resp = _post(client, "something ambiguous")
    assert resp.status_code == 200
    assert resp.json()["id"] is None


def test_model_choosing_null_stays_null(client, model_on, monkeypatch):
    monkeypatch.setattr(
        navigate_module, "complete_json", lambda *a, **k: {"id": None, "reason": "nothing fits"}
    )
    resp = _post(client, "I want to tell the carrier")
    assert resp.status_code == 200
    assert resp.json()["id"] is None


# ---------------------------------------------------------------------------
# Reason is capped and coerced
# ---------------------------------------------------------------------------
def test_reason_is_capped_at_140_chars(client, model_on, monkeypatch):
    monkeypatch.setattr(
        navigate_module,
        "complete_json",
        lambda *a, **k: {"id": "accuracy", "reason": "x" * 500},
    )
    resp = _post(client, "how good is this thing")
    assert len(resp.json()["reason"]) == 140


def test_non_string_reason_becomes_empty(client, model_on, monkeypatch):
    monkeypatch.setattr(
        navigate_module, "complete_json", lambda *a, **k: {"id": "accuracy", "reason": None}
    )
    resp = _post(client, "how good is this thing")
    assert resp.status_code == 200
    assert resp.json()["reason"] == ""


# ---------------------------------------------------------------------------
# The client index and the server's own list must be the same set of ids -
# both read the exact same file, but this proves that file is what backs
# both sides rather than a copy that could drift.
# ---------------------------------------------------------------------------
def test_client_and_server_destination_ids_match():
    with open(DESTINATIONS_PATH, encoding="utf-8") as fh:
        client_destinations = json.load(fh)
    client_ids = {d["id"] for d in client_destinations}

    assert client_ids == navigate_module._VALID_IDS
    assert len(client_ids) >= 25  # spec: ~25-35 entries
    assert len(client_ids) <= 35


def test_every_destination_has_id_title_and_hint():
    with open(DESTINATIONS_PATH, encoding="utf-8") as fh:
        destinations = json.load(fh)
    for entry in destinations:
        assert isinstance(entry.get("id"), str) and entry["id"]
        assert isinstance(entry.get("title"), str) and entry["title"]
        assert isinstance(entry.get("hint"), str) and entry["hint"]
