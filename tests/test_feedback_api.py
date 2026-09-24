from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from adapters import store as store_module
from api.index import app


@pytest.fixture(autouse=True)
def memory_store(monkeypatch):
    for name in (
        "KV_REST_API_URL",
        "KV_REST_API_TOKEN",
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLEARDRAFT_STORE", "memory")
    store_module.reset_store_cache()
    yield
    store_module.reset_store_cache()


@pytest.fixture
def client():
    return TestClient(app)


def signup(client, email="alice@example.com"):
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "correct horse battery"},
    )
    assert response.status_code == 200


def test_feedback_requires_sign_in(client):
    assert client.get("/api/feedback").status_code == 401
    assert client.put(
        "/api/feedback",
        json={"key": "mail-1", "verdict": "flagged", "note": "wrong consignee"},
    ).status_code == 401


def test_feedback_round_trip_and_delete(client):
    signup(client)
    saved = client.put(
        "/api/feedback",
        json={"key": "mine:mail-1", "verdict": "flagged", "note": "wrong consignee"},
    )
    assert saved.status_code == 200
    record = saved.json()["review"]
    assert record["verdict"] == "flagged"
    assert record["note"] == "wrong consignee"
    assert record["at"].endswith("+00:00")

    listed = client.get("/api/feedback")
    assert listed.status_code == 200
    assert listed.json()["reviews"]["mine:mail-1"] == record

    deleted = client.delete("/api/feedback/mine%3Amail-1")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.get("/api/feedback").json() == {"reviews": {}}


def test_feedback_is_isolated_by_account(client):
    signup(client)
    client.put(
        "/api/feedback",
        json={"key": "sample-1", "verdict": "confirmed", "note": ""},
    )

    other = TestClient(app)
    signup(other, "bob@example.com")
    assert other.get("/api/feedback").json() == {"reviews": {}}


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"key": "", "verdict": "flagged", "note": "x"}, "invalid_key"),
        ({"key": "x", "verdict": "maybe", "note": "x"}, "invalid_verdict"),
        ({"key": "x", "verdict": "flagged", "note": ""}, "note_required"),
        ({"key": "x", "verdict": "done", "note": "problem:"}, "note_required"),
    ],
)
def test_feedback_validation(client, payload, error):
    signup(client)
    response = client.put("/api/feedback", json=payload)
    assert response.status_code == 400
    assert response.json()["error"] == error


def test_feedback_corrections_round_trip(client):
    """"Fix a value": PUT /api/feedback's schema, extended additively with
    an optional `corrections` map (api/_feedback.py)."""
    signup(client)
    saved = client.put(
        "/api/feedback",
        json={
            "key": "mine:mail-1",
            "verdict": "confirmed",
            "note": "",
            "corrections": {"consignee": {"si": "EAST BRIGHT FZ-LLC", "bl": "EAST BRIGHT FZ-LLC"}},
        },
    )
    assert saved.status_code == 200
    record = saved.json()["review"]
    assert record["corrections"] == {"consignee": {"si": "EAST BRIGHT FZ-LLC", "bl": "EAST BRIGHT FZ-LLC"}}

    listed = client.get("/api/feedback")
    assert listed.json()["reviews"]["mine:mail-1"]["corrections"] == record["corrections"]


def test_feedback_corrections_are_additive_when_omitted(client):
    """A later PUT that doesn't mention `corrections` (every caller before
    this feature existed) must not silently erase a saved correction."""
    signup(client)
    client.put(
        "/api/feedback",
        json={
            "key": "mine:mail-1",
            "verdict": "confirmed",
            "note": "",
            "corrections": {"consignee": {"si": "A", "bl": "B"}},
        },
    )
    saved = client.put(
        "/api/feedback",
        json={"key": "mine:mail-1", "verdict": "confirmed", "note": "updated note"},
    )
    assert saved.status_code == 200
    assert saved.json()["review"]["corrections"] == {"consignee": {"si": "A", "bl": "B"}}
    assert saved.json()["review"]["note"] == "updated note"


def test_feedback_corrections_reject_unknown_field(client):
    signup(client)
    resp = client.put(
        "/api/feedback",
        json={
            "key": "mine:mail-1",
            "verdict": "confirmed",
            "note": "",
            "corrections": {"not_a_real_field": {"si": "A", "bl": "B"}},
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_corrections"


def test_feedback_corrections_reject_oversized_values(client):
    signup(client)
    resp = client.put(
        "/api/feedback",
        json={
            "key": "mine:mail-1",
            "verdict": "confirmed",
            "note": "",
            "corrections": {"consignee": {"si": "x" * 501, "bl": "y"}},
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_corrections"


def test_feedback_caps_lengths(client):
    signup(client)
    response = client.put(
        "/api/feedback",
        json={"key": "x" * 301, "verdict": "flagged", "note": "valid"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_key"

    response = client.put(
        "/api/feedback",
        json={"key": "x", "verdict": "flagged", "note": "n" * 1001},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "note_too_long"
