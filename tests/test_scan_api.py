"""Tests for POST /api/scan (api/_ocr.py) and adapters.model.transcribe_image.

Hermetic: no network, no real model calls. tests/conftest.py sets
CLEARDRAFT_USE_MODEL=0 for the whole suite, so every test here either
exercises the "model off" 503 path directly, or explicitly flips the switch
on with a fake key and monkeypatches transcribe_image (or, for the one unit
test at the adapter layer, leaves the switch off on purpose) so no real
call ever leaves the process.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

import adapters.model as model_mod
from adapters.model import ModelUnavailable
from api.index import app

client = TestClient(app)

#: Just the PNG magic-byte signature plus a little body - api/_ocr.py's
#: _sniff_media_type only inspects the header, and transcribe_image itself
#: is monkeypatched in every test that gets past that check, so this never
#: needs to be a real, decodable image.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body-not-a-real-image"


def _post_png(data: bytes, filename: str = "photo.png"):
    return client.post(
        "/api/scan", files={"file": (filename, io.BytesIO(data), "image/png")}
    )


def test_missing_file_is_400():
    r = client.post("/api/scan", files={})
    assert r.status_code == 400
    assert r.json()["error"] == "missing_file"


def test_wrong_type_is_400():
    """A plain text file, whatever its declared content-type, is not one of
    the three accepted image formats."""
    r = client.post(
        "/api/scan",
        files={"file": ("note.txt", io.BytesIO(b"hello world"), "text/plain")},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_image_type"


def test_oversize_is_400():
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (4 * 1024 * 1024 + 1)
    r = _post_png(big)
    assert r.status_code == 400
    assert r.json()["error"] == "file_too_large"


def test_png_filename_with_non_image_bytes_is_400():
    """The magic-byte check, not the filename or declared content-type:
    something named photo.png (and sent as image/png) whose actual bytes
    are not a PNG must still be rejected."""
    r = _post_png(b"this is not a real png file at all")
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_image_type"


def test_empty_file_is_400():
    r = _post_png(b"")
    assert r.status_code == 400
    assert r.json()["error"] in ("invalid_image", "unsupported_image_type")


def test_model_off_is_503_with_the_exact_message():
    # tests/conftest.py already sets CLEARDRAFT_USE_MODEL=0 for the suite.
    r = _post_png(_PNG_BYTES)
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "scan_unavailable"
    assert body["detail"] == (
        "Photo reading needs the AI service, which is off right now. "
        "Type or paste the text instead."
    )


def test_happy_path_with_transcribe_image_monkeypatched(monkeypatch):
    monkeypatch.setenv("CLEARDRAFT_USE_MODEL", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    captured = {}

    def fake_transcribe(image_bytes, media_type):
        captured["image_bytes"] = image_bytes
        captured["media_type"] = media_type
        return "Consignee: ACME CO\nPort of Loading: NANTONG"

    monkeypatch.setattr(model_mod, "transcribe_image", fake_transcribe)

    r = _post_png(_PNG_BYTES)
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "Consignee: ACME CO\nPort of Loading: NANTONG"
    assert body["model"] == model_mod.MODEL
    assert isinstance(body["seconds"], (int, float))
    assert captured["media_type"] == "image/png"
    assert captured["image_bytes"] == _PNG_BYTES


def test_transcribe_image_failure_surfaces_as_503(monkeypatch):
    """When the model is switched on but the call itself fails (rate limit,
    timeout, gate...), transcribe_image raises ModelUnavailable and the
    route must still answer with the same clean 503 JSON, not a 500."""
    monkeypatch.setenv("CLEARDRAFT_USE_MODEL", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    def raising_transcribe(image_bytes, media_type):
        raise ModelUnavailable("simulated provider failure")

    monkeypatch.setattr(model_mod, "transcribe_image", raising_transcribe)

    r = _post_png(_PNG_BYTES)
    assert r.status_code == 503
    assert r.json()["error"] == "scan_unavailable"


def test_transcribe_image_raises_when_switch_is_off():
    """Unit test at the adapter layer, not through the API: with
    CLEARDRAFT_USE_MODEL=0 (the hermetic test default from conftest.py),
    transcribe_image must raise ModelUnavailable rather than attempt a
    call - the same contract complete_json already has."""
    with pytest.raises(ModelUnavailable):
        model_mod.transcribe_image(_PNG_BYTES, "image/png")
