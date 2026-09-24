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
    POST /api/process-email    - parse an uploaded .eml (or a pasted-in
                                  subject/body + attachments), run it end to
                                  end through classify/extract/compare/decide,
                                  same as the dataset path in core/pipeline.py
    POST /api/process-dataset  - upload a .zip bundle (inbox/*.json +
                                  attachments/*) and run the whole batch
                                  pipeline over it live: see api/_dataset.py
    /api/auth/*, /api/mail*    - accounts: see api/_accounts.py

Accounts note: /api/auth/* and /api/mail* are owned by api/_accounts.py and
included below as a router. This module only calls `current_user()` to
decide whether a /api/process-email result gets saved to that user's
mailbox - no session/password logic lives here.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
from email.message import EmailMessage

# Repo root is the parent of this file's directory (api/). Insert it once, at
# import time, so `import core` / `import adapters` resolve under Vercel's
# function runtime the same way they do locally.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, File, Form, Request, UploadFile  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from adapters.eml import EmlError, parse_eml, safe_filename  # noqa: E402
from adapters.model import MODEL, STATS  # noqa: E402
from adapters.model import available as model_available, switch_enabled as model_switch_enabled  # noqa: E402
from adapters.store import StoreError, get_store  # noqa: E402
from api._accounts import current_user, router as accounts_router, save_result_to_mailbox  # noqa: E402
from api._dataset import router as dataset_router  # noqa: E402
from api._equivalences import lookup_for, router as equivalences_router  # noqa: E402
from api._feedback import router as feedback_router  # noqa: E402
from core import parsers  # noqa: E402
from core.classify import classify  # noqa: E402
from core.compare import compare  # noqa: E402
from core.decide import decide  # noqa: E402
from core.extract import extract  # noqa: E402
from core.pipeline import split_si_bl  # noqa: E402
from core.reply import FIELD_LABELS, _reference, draft_reply  # noqa: E402
from core.types import Classification, Email  # noqa: E402
from core.variance import comparison_variance  # noqa: E402

app = FastAPI()
app.include_router(accounts_router)
app.include_router(equivalences_router)
app.include_router(feedback_router)
app.include_router(dataset_router)


@app.exception_handler(StoreError)
async def _store_down(request, exc):
    # The account store is unreachable. Say so cleanly; never a raw 500.
    return JSONResponse(
        status_code=503,
        content={"error": "accounts_unavailable", "detail": "Accounts are temporarily unavailable. Try again shortly."},
    )

#: Case-insensitive extensions the four format adapters cover.
_ALLOWED_EXTENSIONS = {".txt", ".pdf", ".xlsx", ".docx"}

#: 2 MB. A file this size or smaller is accepted; anything larger is rejected.
_MAX_BYTES = 2 * 1024 * 1024

#: 4 MB cap for an uploaded .eml. Vercel's own request-body limit is 4.5 MB,
#: so this leaves headroom for multipart framing overhead.
_EML_MAX_BYTES = 4 * 1024 * 1024

#: "Paste an email" mode (no .eml file): caps for the pasted body and the
#: attachments picked from the user's computer.
_PASTE_BODY_MAX_CHARS = 50_000
_PASTE_ATTACH_MAX_FILES = 4
_PASTE_ATTACH_MAX_BYTES = 4 * 1024 * 1024

#: (maintype, subtype) per extension, for building an EmailMessage out of
#: pasted-mode attachments so they round-trip through parse_eml() exactly
#: like a real .eml's attachments do.
_ATTACHMENT_MIME = {
    ".txt": ("text", "plain"),
    ".pdf": ("application", "pdf"),
    ".xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
}

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
        "accounts": get_store() is not None,
        "parsers": list(parsers.supported()),
        "missing_parsers": parsers.MISSING_PARSERS,
        # Short commit SHA this deployment was built from, for telling two
        # live deploys apart at a glance. "local" outside Vercel, where the
        # env var Vercel injects at build time is not set.
        "commit": (os.environ.get("VERCEL_GIT_COMMIT_SHA") or "local")[:7],
        "model": MODEL,
        "model_switch": "on" if model_switch_enabled() else "off",
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
        "placement_rejections": STATS.placement_rejections,
        "input_tokens": STATS.input_tokens,
        "output_tokens": STATS.output_tokens,
    }


@app.post("/api/check")
async def check(
    request: Request,
    si: "UploadFile | None" = File(None),
    bl: "UploadFile | None" = File(None),
    subject: str = Form(""),
    body: str = Form(""),
):
    started = time.time()
    known_equal = lookup_for(request)

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
            comparisons = compare(si_doc, bl_doc, known_equal=known_equal)

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
            "variance_reason": comparison_variance(c),
            "learned": c.matched and c.si_norm != c.bl_norm,
            "si_norm": c.si_norm,
            "bl_norm": c.bl_norm,
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


def _process_eml_bytes(data: bytes, upload_name: str, known_equal=None) -> "dict | JSONResponse":
    """The one pipeline: raw .eml bytes in, {board, detail, model_delta} out.

    Shared by both `/api/process-email` request shapes - an uploaded .eml
    file and a pasted subject/body (+ up to 4 attachments) that gets
    serialised into the same RFC 5322 bytes first - so there is exactly one
    copy of the classify -> extract -> compare -> decide -> draft_reply
    chain, same as core/pipeline.process and scripts/export_ui_data.py.

    `known_equal`, when given, is api._equivalences.lookup_for(request)'s
    result - the signed-in caller's learned pairs, or None when signed out
    or accounts are unavailable. Passed straight through to compare().

    Nothing is written anywhere except a per-request tempfile.TemporaryDirectory,
    which is removed before this function returns. No email content is logged.

    Returns a JSONResponse directly when `data` does not parse as an email;
    callers must check for that before touching the result as a dict.
    """
    try:
        parsed = parse_eml(data)
    except EmlError as exc:
        return _error(400, "not_an_email", str(exc))

    # Dedupe re-uploads of the same bytes: same content -> same email_id,
    # every time, with no state kept between requests.
    email_id = "up_" + hashlib.sha1(data).hexdigest()[:12]

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
            comparisons = compare(si, bl, known_equal=known_equal)

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
                "variance_reason": comparison_variance(c),
                "learned": c.matched and c.si_norm != c.bl_norm,
                "si_norm": c.si_norm,
                "bl_norm": c.bl_norm,
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
            "confidence": classification.confidence,
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

    return {"board": board, "detail": detail, "model_delta": model_delta}


@app.post("/api/process-email")
async def process_email(
    request: Request,
    eml: "UploadFile | None" = File(None),
    subject: str = Form(""),
    body: "str | None" = Form(None),
    sender: str = Form(""),
    files: "list[UploadFile]" = File(default=[]),
):
    """Upload-your-own-email demo path, in two request shapes:

    1. An uploaded `eml` file (.eml, <= 4 MB) - unchanged from before.
    2. "Paste an email": no `eml` file, but a pasted `subject`/`body` (body
       required, <= 50,000 chars) plus 0-4 `files` (.txt/.pdf/.xlsx/.docx,
       4 MB total) picked from the user's computer, for people who have the
       email's text but not a saved .eml. This is built into the same RFC
       5322 bytes an .eml upload would be and run through the identical
       pipeline (`_process_eml_bytes`) - no duplicated logic.

    When the caller is signed in (a valid `cd_session` cookie), the result
    is also saved into that user's mailbox and the response carries
    "saved": true; signed out, it carries "saved": false. Nothing is
    written anywhere except a per-request tempfile.TemporaryDirectory. No
    email content is logged.
    """
    started = time.time()

    if eml is not None and eml.filename:
        ext = os.path.splitext(eml.filename)[1].lower()
        if ext != ".eml":
            return _error(
                400, "unsupported_extension", f"'eml' file extension {ext!r} is not supported"
            )
        data = await _read_capped(eml, _EML_MAX_BYTES)
        if data is None:
            return _error(400, "file_too_large", "'eml' file exceeds 4 MB")
        # Basename only, for display - never used to build a filesystem path.
        upload_name = eml.filename.replace("\\", "/").rsplit("/", 1)[-1]
    else:
        if body is None or not body.strip():
            return _error(
                400, "missing_email", "Upload a .eml file or paste the email text."
            )
        if len(body) > _PASTE_BODY_MAX_CHARS:
            return _error(
                400, "body_too_long", "Pasted email body exceeds 50,000 characters."
            )
        if len(files) > _PASTE_ATTACH_MAX_FILES:
            return _error(400, "too_many_files", "Attach at most 4 files.")

        attachments: "list[tuple[str, bytes]]" = []
        total_bytes = 0
        for upload in files:
            if upload is None or not upload.filename:
                continue
            attachment_ext = os.path.splitext(upload.filename)[1].lower()
            if attachment_ext not in _ALLOWED_EXTENSIONS:
                return _error(
                    400,
                    "unsupported_extension",
                    f"attachment extension {attachment_ext!r} is not supported",
                )
            remaining = _PASTE_ATTACH_MAX_BYTES - total_bytes
            content = await upload.read(remaining + 1)
            total_bytes += len(content)
            if total_bytes > _PASTE_ATTACH_MAX_BYTES:
                return _error(400, "file_too_large", "attachments exceed 4 MB total")
            name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
            attachments.append((name, content))

        msg = EmailMessage()
        msg["From"] = (sender or "").strip() or "unknown@pasted.local"
        msg["Subject"] = subject or ""
        msg.set_content(body)
        for name, content in attachments:
            maintype, subtype = _ATTACHMENT_MIME.get(
                os.path.splitext(name)[1].lower(), ("application", "octet-stream")
            )
            msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=name)
        data = bytes(msg)
        upload_name = "Pasted email"

    known_equal = lookup_for(request)
    result = _process_eml_bytes(data, upload_name, known_equal=known_equal)
    if isinstance(result, JSONResponse):
        return result

    board, detail, model_delta = result["board"], result["detail"], result["model_delta"]

    # Save into the signed-in user's mailbox, if any. The mailbox is capped
    # at MAX_MAILBOX by dropping the oldest entries (see
    # api._accounts.merge_mailbox), so it always makes room for one more -
    # "save_failed" below is only ever hit if saving itself raises (e.g. a
    # storage backend error), not because the cap could not be enforced.
    saved = False
    save_error = None
    user_email = current_user(request)
    if user_email is not None:
        try:
            save_result_to_mailbox(get_store(), user_email, board, detail)
            saved = True
        except Exception:
            save_error = "save_failed"

    payload = {
        "board": board,
        "detail": detail,
        "model": {"available": model_available(), **model_delta},
        "seconds": round(time.time() - started, 2),
        "saved": saved,
    }
    if not saved and save_error:
        payload["save_error"] = save_error
    return payload
