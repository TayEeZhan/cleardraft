"""Live-check HTTP API. A TRANSPORT LAYER ONLY - every decision is made by
`core`; this module reads uploads, calls the pipeline stages, and serialises
the result.

OWNER: whoever is told to build the live-check API. Deployed on Vercel.
`vercel.json` rewrites `/api/(.*)` to `/api/index`, and the function receives
the ORIGINAL path, so routes below are registered with the full `/api/...`
prefix.

Routes:
    GET  /api/health          - liveness + parser/model availability
    POST /api/check            - compare an uploaded SI against an uploaded BL
    POST /api/process-email    - parse an uploaded .eml, run it end to end
                                  through classify/extract/compare/decide,
                                  same as the dataset path in core/pipeline.py
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time

# Repo root is the parent of this file's directory (api/). Insert it once, at
# import time, so `import core` / `import adapters` resolve under Vercel's
# function runtime the same way they do locally.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, File, Form, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from adapters.eml import EmlError, parse_eml, safe_filename  # noqa: E402
from adapters.model import STATS, available as model_available  # noqa: E402
from core import parsers  # noqa: E402
from core.classify import classify  # noqa: E402
from core.compare import compare  # noqa: E402
from core.decide import decide  # noqa: E402
from core.extract import extract  # noqa: E402
from core.pipeline import split_si_bl  # noqa: E402
from core.reply import FIELD_LABELS, _reference, draft_reply  # noqa: E402
from core.types import Classification, Email  # noqa: E402

app = FastAPI()

#: Case-insensitive extensions the four format adapters cover.
_ALLOWED_EXTENSIONS = {".txt", ".pdf", ".xlsx", ".docx"}

#: 2 MB. A file this size or smaller is accepted; anything larger is rejected.
_MAX_BYTES = 2 * 1024 * 1024

#: 4 MB cap for an uploaded .eml. Vercel's own request-body limit is 4.5 MB,
#: so this leaves headroom for multipart framing overhead.
_EML_MAX_BYTES = 4 * 1024 * 1024

#: The web form IS an explicit request to compare - never the classifier's
#: own read of subject/body. See spec step 5.
_FORCED_COMPARE = Classification(
    category="BL_COMPARISON",
    intent="compare",
    decided_by="rule",
    confidence=1.0,
    evidence="comparison requested through the web checker",
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model_available": model_available(),
        "parsers": list(parsers.supported()),
        "missing_parsers": parsers.MISSING_PARSERS,
    }


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


async def _read_capped(upload: UploadFile, max_bytes: int = _MAX_BYTES) -> "bytes | None":
    """Read at most max_bytes + 1 bytes. Returns None when that is oversized.

    Reading one byte past the cap is enough to decide "too big" without ever
    holding a large upload fully in memory just to reject it.
    """
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None
    return data


def _fv(field_value, source: str) -> "dict | None":
    """Mirror scripts/export_ui_data.py's `_fv` shape so the UI's existing
    comparison-row renderer works unchanged against this endpoint."""
    if field_value is None:
        return None
    return {
        "value": field_value.value,
        "raw": field_value.raw,
        "line_no": field_value.line_no,
        "label": field_value.label,
        "decided_by": field_value.decided_by,
        "source": source,
    }


def _doc_info(doc, name: str) -> dict:
    return {
        "name": name,
        "kind": doc.kind,
        "readable": doc.readable,
        "error": doc.error,
    }


def _export_doc(doc, name: "str | None" = None) -> "dict | None":
    """Mirror scripts/export_ui_data.py's `_doc` shape exactly, so the UI's
    existing document-panel renderer works unchanged against this endpoint."""
    if doc is None:
        return None
    return {
        # The display name, never the server's temp path.
        "path": name or os.path.basename(doc.path),
        "kind": doc.kind,
        "readable": doc.readable,
        "error": doc.error,
        "text": doc.text,
    }


def _stats_snapshot() -> dict:
    return {
        "calls": STATS.calls,
        "gate_rejections": STATS.gate_rejections,
        "input_tokens": STATS.input_tokens,
        "output_tokens": STATS.output_tokens,
    }


@app.post("/api/check")
async def check(
    si: "UploadFile | None" = File(None),
    bl: "UploadFile | None" = File(None),
    subject: str = Form(""),
    body: str = Form(""),
):
    started = time.time()

    if si is None or not si.filename:
        return _error(400, "missing_file", "'si' file is required")
    if bl is None or not bl.filename:
        return _error(400, "missing_file", "'bl' file is required")

    # Extension is read from the client's filename for its SUFFIX only. The
    # filename itself is never used as a path - see the tempfile block below.
    si_ext = os.path.splitext(si.filename)[1].lower()
    bl_ext = os.path.splitext(bl.filename)[1].lower()
    if si_ext not in _ALLOWED_EXTENSIONS:
        return _error(
            400, "unsupported_extension", f"'si' file extension {si_ext!r} is not supported"
        )
    if bl_ext not in _ALLOWED_EXTENSIONS:
        return _error(
            400, "unsupported_extension", f"'bl' file extension {bl_ext!r} is not supported"
        )

    si_bytes = await _read_capped(si)
    if si_bytes is None:
        return _error(400, "file_too_large", "'si' file exceeds 2 MB")
    bl_bytes = await _read_capped(bl)
    if bl_bytes is None:
        return _error(400, "file_too_large", "'bl' file exceeds 2 MB")

    before = _stats_snapshot()

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Fixed names, never the client's filename - that is how a path like
        # "../../evil.txt" stays confined to this directory.
        si_path = os.path.join(tmp_dir, f"si{si_ext}")
        bl_path = os.path.join(tmp_dir, f"bl{bl_ext}")
        with open(si_path, "wb") as fh:
            fh.write(si_bytes)
        with open(bl_path, "wb") as fh:
            fh.write(bl_bytes)

        si_doc = extract(si_path)
        bl_doc = extract(bl_path)

        email = Email(
            email_id="web-check",
            sender="",
            subject=subject or "",
            body=body or "",
            attachments=(f"si{si_ext}", f"bl{bl_ext}"),
        )

        email_reading = None
        if (subject or "").strip() or (body or "").strip():
            reading = classify(email)
            email_reading = {
                "category": reading.category,
                "intent": reading.intent,
                "decided_by": reading.decided_by,
                "evidence": reading.evidence,
            }

        # Same rule as core/pipeline.py: only compare when both documents were
        # actually read.
        comparisons = ()
        if si_doc.readable and bl_doc.readable:
            comparisons = compare(si_doc, bl_doc)

        decision = decide(email, _FORCED_COMPARE, si_doc, bl_doc, comparisons)
        reply_draft = draft_reply(email, decision)

    after = _stats_snapshot()
    model_delta = {key: after[key] - before[key] for key in before}

    comparison_rows = [
        {
            "field": c.field,
            "label": FIELD_LABELS.get(c.field, c.field),
            "matched": c.matched,
            "undecidable": c.undecidable,
            "si": _fv(c.si, si.filename),
            "bl": _fv(c.bl, bl.filename),
        }
        for c in comparisons
    ]

    payload = {
        "reference": _reference(email) or None,
        "email_reading": email_reading,
        "status": decision.status,
        "review_reason": decision.review_reason,
        "rationale": decision.rationale,
        "category": decision.category,
        "decided_by": decision.decided_by,
        "defect_fields": list(decision.defect_fields),
        "documents": {
            "si": _doc_info(si_doc, si.filename),
            "bl": _doc_info(bl_doc, bl.filename),
        },
        "comparisons": comparison_rows,
        "reply_draft": reply_draft,
        "model": {"available": model_available(), **model_delta},
        "seconds": round(time.time() - started, 2),
        "subject": email.subject,
        "from": "",
    }
    return payload


@app.post("/api/process-email")
async def process_email(eml: "UploadFile | None" = File(None)):
    """Upload-your-own-email demo path.

    Parses one .eml file (adapters/eml.py), builds a core.types.Email from
    it, and runs the exact same classify -> extract -> compare -> decide ->
    draft_reply chain as core/pipeline.process and scripts/export_ui_data.py,
    so the response can be dropped straight into the same board/detail
    renderers the dataset-backed UI already uses.

    Nothing is written anywhere except a per-request tempfile.TemporaryDirectory,
    which is removed before this function returns. No email content is logged.
    """
    started = time.time()

    if eml is None or not eml.filename:
        return _error(400, "unsupported_extension", "'eml' file is required")

    ext = os.path.splitext(eml.filename)[1].lower()
    if ext != ".eml":
        return _error(
            400, "unsupported_extension", f"'eml' file extension {ext!r} is not supported"
        )

    data = await _read_capped(eml, _EML_MAX_BYTES)
    if data is None:
        return _error(400, "file_too_large", "'eml' file exceeds 4 MB")

    try:
        parsed = parse_eml(data)
    except EmlError as exc:
        return _error(400, "not_an_email", str(exc))

    # Dedupe re-uploads of the same file: same bytes -> same email_id, every
    # time, with no state kept between requests.
    email_id = "up_" + hashlib.sha1(data).hexdigest()[:12]

    # Basename only, for display - never used to build a filesystem path.
    upload_name = eml.filename.replace("\\", "/").rsplit("/", 1)[-1]

    before = _stats_snapshot()

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Only attachments in a supported format are written to disk and run
        # through the pipeline; the rest are reported back as skipped so the
        # UI can say why they were not compared.
        saved_names: list[str] = []
        original_by_saved: dict[str, str] = {}
        original_names: list[str] = []
        skipped_attachments: list[str] = []

        for index, (orig_name, content) in enumerate(parsed.attachments):
            original_names.append(orig_name)
            attachment_ext = os.path.splitext(orig_name)[1].lower()
            if attachment_ext not in _ALLOWED_EXTENSIONS:
                skipped_attachments.append(orig_name)
                continue
            saved = safe_filename(orig_name, index)
            path = os.path.join(tmp_dir, saved)
            with open(path, "wb") as fh:
                fh.write(content)
            saved_names.append(saved)
            original_by_saved[saved] = orig_name

        email_obj = Email(
            email_id=email_id,
            sender=parsed.sender,
            subject=parsed.subject,
            body=parsed.body,
            attachments=tuple(saved_names),
        )

        # Same shape as core.pipeline.process: classify first, and only read
        # attachments off disk when the classifier actually calls for it.
        classification = classify(email_obj)

        docs = []
        if classification.category == "BL_COMPARISON":
            docs = [extract(os.path.join(tmp_dir, name)) for name in saved_names]

        si, bl = split_si_bl(docs)

        comparisons = ()
        if si is not None and bl is not None and si.readable and bl.readable:
            comparisons = compare(si, bl)

        decision = decide(email_obj, classification, si, bl, comparisons)
        reply_draft = draft_reply(email_obj, decision)
        reference = _reference(email_obj)

        def _source_name(doc) -> str:
            if doc is None:
                return ""
            saved = os.path.basename(doc.path)
            return original_by_saved.get(saved, saved)

        comparison_rows = [
            {
                "field": c.field,
                "label": FIELD_LABELS.get(c.field, c.field),
                "matched": c.matched,
                "undecidable": c.undecidable,
                "si": _fv(c.si, _source_name(si)),
                "bl": _fv(c.bl, _source_name(bl)),
            }
            for c in comparisons
        ]

        board = {
            "email_id": email_obj.email_id,
            "subject": email_obj.subject,
            "from": email_obj.sender,
            "reference": reference,
            "category": decision.category,
            "status": decision.status,
            "review_reason": decision.review_reason,
            "has_defect": decision.has_defect,
            "defect_fields": list(decision.defect_fields),
            "attachment_count": len(email_obj.attachments),
            "decided_by": decision.decided_by,
        }

        detail = {
            "email_id": email_obj.email_id,
            "subject": email_obj.subject,
            "from": email_obj.sender,
            "body": email_obj.body,
            "reference": reference,
            "category": decision.category,
            "intent": classification.intent,
            "evidence": classification.evidence,
            "status": decision.status,
            "review_reason": decision.review_reason,
            "rationale": decision.rationale,
            "decided_by": decision.decided_by,
            "defect_fields": list(decision.defect_fields),
            "documents": {"si": _export_doc(si, _source_name(si)), "bl": _export_doc(bl, _source_name(bl))},
            "comparisons": comparison_rows,
            "reply_draft": reply_draft,
            "recheck": None,
            "uploaded": True,
            "filename": upload_name,
            "received": parsed.date,
            "attachments": original_names,
            "skipped_attachments": skipped_attachments,
        }

    after = _stats_snapshot()
    model_delta = {key: after[key] - before[key] for key in before}

    return {
        "board": board,
        "detail": detail,
        "model": {"available": model_available(), **model_delta},
        "seconds": round(time.time() - started, 2),
    }
