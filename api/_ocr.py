"""POST /api/scan - transcribe a photo of a Shipping Instruction or draft
Bill of Lading (or the email itself) into editable plain text, via the one
door every model call passes through (adapters.model.transcribe_image).

OWNER: whoever built "Scan a document (photo)". A TRANSPORT LAYER ONLY -
this module validates one uploaded image, sets a small per-request model
budget, and calls transcribe_image. No pipeline logic lives here - see
docs/API.md's "one thing to get right".

Design principle: AI never decides. This endpoint only reads text off a
photo; the clerk reviews and edits every line in the UI (web/scan.js)
before anything derived from it goes anywhere near the pipeline. A
transcription from here never touches core/extract.py's
verify_against_source gate, because it never becomes a FieldValue by
itself - after the clerk confirms it, it becomes a plain .txt "attachment"
that goes through POST /api/process-email exactly like any other file a
clerk picked from their computer (see api/index.py's paste-mode path).

Included as a router from api/index.py, same pattern as api/_dataset.py and
api/_equivalences.py - this file starts with "_" because every
non-underscore file in api/ becomes its own Vercel function, and this
endpoint shares the one deployed function with everything else here.

Safety, because this endpoint runs on an anonymous upload of an untrusted
photo, with no sign-in required:
  - the request body is read with a hard cap (_MAX_BYTES), the same "read
    one byte past the cap" discipline every other upload endpoint in this
    API uses, so an oversized photo is never held fully in memory just to
    reject it;
  - the image type is decided from the ACTUAL bytes (magic-byte sniffing),
    never from the client-supplied filename extension or Content-Type,
    both of which are trivially spoofable;
  - the model tier gets a small, per-request budget - 2 attempts and 30
    real seconds - via the same adapters.model._BUDGET contextvar
    api/_dataset.py uses for its whole batch run, so one slow or failing
    photo cannot hold this endpoint open indefinitely;
  - the image bytes are never written to disk and never logged - they live
    only in this request's memory, for exactly as long as the model call
    needs them.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

import adapters.model as model_mod
from adapters.model import MODEL, ModelUnavailable
from adapters.model import available as model_available

router = APIRouter()

#: 4 MB. Vercel's own request-body limit is 4.5 MB, so this leaves headroom
#: for multipart framing overhead - the same margin api/index.py's
#: _EML_MAX_BYTES leaves for an uploaded .eml. web/scan.js downscales a
#: phone photo client-side before it ever reaches this cap.
_MAX_BYTES = 4 * 1024 * 1024

#: This request's whole model budget: at most this many attempts (success
#: OR failure - see adapters/model.py's _BUDGET docstring for why a failed
#: attempt must count too) and at most this many real seconds, whichever
#: comes first. One photo is one small job, unlike api/_dataset.py's
#: whole-batch budget.
_MODEL_ATTEMPT_BUDGET = 2
_MODEL_TIME_BUDGET_SECONDS = 30.0

#: Magic-byte signatures for the three formats the client's
#: <input accept="image/*"> is expected to send, checked against the actual
#: bytes only.
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_WEBP_RIFF = b"RIFF"
_WEBP_TAG = b"WEBP"

#: The exact wording the spec asks for when the model tier is off or
#: unavailable, shown to the clerk as the reason (web/scan.js falls back to
#: an empty, editable textarea alongside this message).
_UNAVAILABLE_DETAIL = (
    "Photo reading needs the AI service, which is off right now. "
    "Type or paste the text instead."
)


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


def _sniff_media_type(data: bytes) -> "str | None":
    """The image media type the bytes actually are, or None when they
    match none of the three accepted formats. Never trusts a filename or a
    client-supplied Content-Type header - both are attacker-controlled."""
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if len(data) >= 12 and data[:4] == _WEBP_RIFF and data[8:12] == _WEBP_TAG:
        return "image/webp"
    return None


async def _read_capped(upload: UploadFile, max_bytes: int) -> "bytes | None":
    """Read at most max_bytes + 1 bytes. None means "too big" - mirrors
    api/index.py's _read_capped so an oversized upload is never held fully
    in memory just to reject it."""
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None
    return data


@router.post("/api/scan")
async def scan(file: "UploadFile | None" = File(None)):
    started = time.time()

    if file is None or not file.filename:
        return _error(400, "missing_file", "'file' is required (one photo)")

    data = await _read_capped(file, _MAX_BYTES)
    if data is None:
        return _error(
            400, "file_too_large", f"file exceeds {_MAX_BYTES // (1024 * 1024)} MB"
        )
    if not data:
        return _error(400, "invalid_image", "the uploaded file is empty")

    media_type = _sniff_media_type(data)
    if media_type is None:
        return _error(
            400, "unsupported_image_type",
            "only JPEG, PNG or WEBP photos are supported",
        )

    if not model_available():
        return _error(503, "scan_unavailable", _UNAVAILABLE_DETAIL)

    # [attempts_left, deadline] - a small, request-scoped budget so one
    # photo can burn at most 2 attempts / 30 real seconds. Same mechanism
    # api/_dataset.py sets around its whole batch run; reset in `finally` so
    # this request's budget never leaks onto anything else sharing this
    # thread's context afterward.
    budget = [_MODEL_ATTEMPT_BUDGET, time.monotonic() + _MODEL_TIME_BUDGET_SECONDS]
    token = model_mod._BUDGET.set(budget)
    try:
        try:
            text = model_mod.transcribe_image(data, media_type)
        except ModelUnavailable:
            return _error(503, "scan_unavailable", _UNAVAILABLE_DETAIL)
    finally:
        model_mod._BUDGET.reset(token)

    return {"text": text, "model": MODEL, "seconds": round(time.time() - started, 2)}


__all__ = ["router"]
