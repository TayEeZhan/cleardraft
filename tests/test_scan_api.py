"""Tests for POST /api/scan (api/_ocr.py) and adapters.model.transcribe_image.

Hermetic: no network, no real model calls. tests/conftest.py sets
CLEARDRAFT_USE_MODEL=0 for the whole suite, so every test here either
exercises the "model off" 503 path directly, or explicitly flips the switch
on with a fake key and monkeypatches either transcribe_image (API-layer
tests) or anthropic.Anthropic itself (adapter-layer tests, which exercise
transcribe_image's real BadRequestError/stop_reason handling) so no real
call ever leaves the process.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

import adapters.model as model_mod
from adapters.model import ImageRejected, ModelUnavailable
from api._ocr import _MAX_BYTES
from api.index import app

client = TestClient(app)

#: Just the PNG magic-byte signature plus a little body - api/_ocr.py's
#: _sniff_media_type only inspects the header, and transcribe_image itself
#: is monkeypatched in every API-layer test that gets past that check, so
#: this never needs to be a real, decodable image.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body-not-a-real-image"


def _post_png(data: bytes, filename: str = "photo.png"):
    return client.post(
        "/api/scan", files={"file": (filename, io.BytesIO(data), "image/png")}
    )


def _model_on(monkeypatch):
    monkeypatch.setenv("CLEARDRAFT_USE_MODEL", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


# ---------------------------------------------------------------------------
# Upload validation (no model involvement at all)
# ---------------------------------------------------------------------------

def test_missing_file_is_400():
    r = client.post("/api/scan", files={})
    assert r.status_code == 400
    assert r.json()["error"] == "missing_file"


def test_empty_filename_is_400_not_a_bare_422():
    """A multipart part sent with filename="" is parsed by Starlette as a
    plain form VALUE, not a file - if the route parameter were typed as a
    strict `UploadFile | None`, FastAPI's own pydantic validation would
    reject this with a bare 422 {"detail": [...]} before the route body
    ever ran, breaking the {"error", "detail"} shape every route in this
    API uses. Must come back as our own 400 missing_file instead."""
    r = client.post(
        "/api/scan", files={"file": ("", io.BytesIO(b""), "image/png")}
    )
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


def test_max_bytes_matches_anthropics_base64_image_limit():
    """Anthropic's Messages API rejects an image over 5 MB once base64
    encoded (base64 inflates by 4/3). The raw-upload cap must be 3/4 of
    that, with headroom to spare for the JSON request's own overhead -
    not the old flat 4 MB (which base64-encodes to ~5.33 MB, already over
    the limit)."""
    assert _MAX_BYTES == (5 * 1024 * 1024) * 3 // 4 - 4096
    assert _MAX_BYTES < 5 * 1024 * 1024  # sanity: well under the raw cap too


def test_oversize_is_400():
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (_MAX_BYTES + 1)
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


# ---------------------------------------------------------------------------
# Model-off / model-unavailable
# ---------------------------------------------------------------------------

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


def test_transcribe_image_failure_surfaces_as_503(monkeypatch):
    """When the model is switched on but the call itself fails (rate limit,
    timeout, gate...), transcribe_image raises ModelUnavailable and the
    route must still answer with the same clean 503 JSON, not a 500."""
    _model_on(monkeypatch)

    def raising_transcribe(image_bytes, media_type):
        raise ModelUnavailable("simulated provider failure")

    monkeypatch.setattr(model_mod, "transcribe_image", raising_transcribe)

    r = _post_png(_PNG_BYTES)
    assert r.status_code == 503
    assert r.json()["error"] == "scan_unavailable"


# ---------------------------------------------------------------------------
# API-layer happy path / truncation / image-rejected (transcribe_image
# monkeypatched directly - these test api/_ocr.py's own handling, not
# transcribe_image's internals)
# ---------------------------------------------------------------------------

def test_happy_path_with_transcribe_image_monkeypatched(monkeypatch):
    _model_on(monkeypatch)

    captured = {}

    def fake_transcribe(image_bytes, media_type):
        captured["image_bytes"] = image_bytes
        captured["media_type"] = media_type
        return model_mod.TranscribeResult(
            text="Consignee: ACME CO\nPort of Loading: NANTONG", truncated=False
        )

    monkeypatch.setattr(model_mod, "transcribe_image", fake_transcribe)

    r = _post_png(_PNG_BYTES)
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "Consignee: ACME CO\nPort of Loading: NANTONG"
    assert body["model"] == model_mod.MODEL
    assert isinstance(body["seconds"], (int, float))
    assert body["truncated"] is False
    assert captured["media_type"] == "image/png"
    assert captured["image_bytes"] == _PNG_BYTES


def test_truncated_flag_surfaces_when_the_model_hit_max_tokens(monkeypatch):
    _model_on(monkeypatch)

    def fake_transcribe(image_bytes, media_type):
        return model_mod.TranscribeResult(text="partial reading...", truncated=True)

    monkeypatch.setattr(model_mod, "transcribe_image", fake_transcribe)

    r = _post_png(_PNG_BYTES)
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "partial reading..."
    assert body["truncated"] is True


def test_image_rejected_is_400_not_503(monkeypatch):
    """A photo the model itself rejects (too large once base64-encoded,
    corrupt, unsupported) is a problem with THIS image, not a service
    outage - it must come back as 400 image_rejected, distinct from the
    503 scan_unavailable the model-off/unreachable path uses."""
    _model_on(monkeypatch)

    def rejecting_transcribe(image_bytes, media_type):
        raise ImageRejected("simulated: model rejected the image")

    monkeypatch.setattr(model_mod, "transcribe_image", rejecting_transcribe)

    r = _post_png(_PNG_BYTES)
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "image_rejected"
    assert body["detail"] == "This photo couldn't be read. Try a smaller or clearer photo."


def test_budget_propagates_into_the_threadpool_worker(monkeypatch):
    """transcribe_image now runs via starlette's run_in_threadpool (so a
    slow model call never blocks the event loop). The per-request _BUDGET
    contextvar set on the request's own async context must still be
    visible inside that worker thread - the same contextvar-propagation
    guarantee api/_dataset.py's _run_pipeline already relies on."""
    _model_on(monkeypatch)

    seen = {}

    def fake_transcribe(image_bytes, media_type):
        seen["budget"] = model_mod._BUDGET.get()
        return model_mod.TranscribeResult(text="ok", truncated=False)

    monkeypatch.setattr(model_mod, "transcribe_image", fake_transcribe)

    r = _post_png(_PNG_BYTES)
    assert r.status_code == 200
    budget = seen.get("budget")
    assert budget is not None, "the _BUDGET contextvar did not propagate into the worker thread"
    assert budget[0] == 2  # _MODEL_ATTEMPT_BUDGET, untouched by this fake
    # The budget must be reset back to None once the request is done, same
    # as every other caller of _BUDGET.set()/.reset().
    assert model_mod._BUDGET.get() is None


# ---------------------------------------------------------------------------
# Adapter-layer unit tests: transcribe_image's own SDK-facing behaviour,
# with a fake anthropic.Anthropic client (mirrors tests/test_model_budget.py's
# pattern) rather than the API route.
# ---------------------------------------------------------------------------

class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens=10, output_tokens=20):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage()
        self.stop_reason = stop_reason


def _client_returning(response):
    class _Messages:
        def create(self, **kwargs):
            return response

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    return _Client


def _client_raising(exc, call_counter=None):
    class _Messages:
        def create(self, **kwargs):
            if call_counter is not None:
                call_counter["n"] += 1
            raise exc

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    return _Client


def _fake_bad_request_error(detail="image too large"):
    import anthropic
    import httpx2

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx2.Response(400, request=req, json={"error": {"message": detail}})
    return anthropic.BadRequestError(detail, response=resp, body=None)


def test_transcribe_image_detects_truncation_from_stop_reason(monkeypatch):
    import anthropic

    _model_on(monkeypatch)
    monkeypatch.setattr(
        anthropic, "Anthropic", _client_returning(_FakeResponse("cut off partway", stop_reason="max_tokens"))
    )

    result = model_mod.transcribe_image(_PNG_BYTES, "image/png")
    assert result.text == "cut off partway"
    assert result.truncated is True


def test_transcribe_image_not_truncated_on_a_normal_stop(monkeypatch):
    import anthropic

    _model_on(monkeypatch)
    monkeypatch.setattr(
        anthropic, "Anthropic", _client_returning(_FakeResponse("the whole document", stop_reason="end_turn"))
    )

    result = model_mod.transcribe_image(_PNG_BYTES, "image/png")
    assert result.text == "the whole document"
    assert result.truncated is False


def test_transcribe_image_raises_image_rejected_on_bad_request(monkeypatch):
    import anthropic

    _model_on(monkeypatch)
    exc = _fake_bad_request_error()
    monkeypatch.setattr(anthropic, "Anthropic", _client_raising(exc))

    with pytest.raises(ImageRejected):
        model_mod.transcribe_image(_PNG_BYTES, "image/png")


def test_transcribe_image_bad_request_is_never_retried(monkeypatch):
    """A bad-image rejection fails identically on a second attempt, so it
    must cost exactly one call, not two - unlike a transient failure,
    which complete_json/transcribe_image both retry once."""
    import anthropic

    _model_on(monkeypatch)
    exc = _fake_bad_request_error()
    calls = {"n": 0}
    monkeypatch.setattr(anthropic, "Anthropic", _client_raising(exc, calls))

    with pytest.raises(ImageRejected):
        model_mod.transcribe_image(_PNG_BYTES, "image/png")
    assert calls["n"] == 1


def test_transcribe_image_raises_when_switch_is_off():
    """Unit test at the adapter layer, not through the API: with
    CLEARDRAFT_USE_MODEL=0 (the hermetic test default from conftest.py),
    transcribe_image must raise ModelUnavailable rather than attempt a
    call - the same contract complete_json already has."""
    with pytest.raises(ModelUnavailable):
        model_mod.transcribe_image(_PNG_BYTES, "image/png")
