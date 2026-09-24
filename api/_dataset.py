"""POST /api/process-dataset - upload a .zip bundle (an `inbox/*.json` +
`attachments/*` folder, the same layout as this repo's `data/`) and run the
whole batch pipeline over it, live, in one request.

OWNER: whoever built the dataset-upload demo path. A TRANSPORT LAYER ONLY -
this module validates and extracts an untrusted zip into a private
`tempfile.TemporaryDirectory`, builds a `LocalInbox` over it, and calls the
exact same row-building function (`adapters.ui_rows.build_rows`) that
`scripts/export_ui_data.py` uses for the full 520-email snapshot, then
serialises the result. No pipeline logic lives here - see `docs/API.md`'s
"one thing to get right".

Included as a router from `api/index.py`, same pattern as `api/_accounts.py`
and `api/_equivalences.py` - this file starts with `_` because every
non-underscore file in `api/` becomes its own Vercel function, and this
demo path shares the one deployed function with everything else in `api/`.

Safety, because this endpoint runs on an anonymous upload of untrusted
bytes, with no sign-in required:

  - the request body is read with a hard cap (`_MAX_UPLOAD_BYTES`), the same
    "read one byte past the cap" discipline `api/index.py` uses, so an
    oversized upload is never held fully in memory just to reject it;
  - a non-zip file is rejected before anything is extracted;
  - zip-slip: any member whose path is absolute, carries a Windows drive
    letter, or has a ".." segment gets the WHOLE zip rejected, checked
    against every member before any bytes are extracted;
  - zip-bomb: member count and total uncompressed size (from each
    `ZipInfo.file_size`, i.e. what the archive *claims* before anything is
    read) are capped, and enforced a second time while actually reading
    bytes out - so a crafted archive whose real decompressed size disagrees
    with its own metadata still cannot inflate past the cap;
  - only `<root>/inbox/*.json` (direct children) and `<root>/attachments/**`
    are ever extracted or read - an answer key such as `ground_truth.json`
    sitting anywhere else in the zip is never opened, whatever it contains;
  - every inbox/*.json is decoded and validated (UTF-8, JSON object, a
    constructible Email) BEFORE it is written to the extracted bundle - a
    malformed record rejects the whole upload with 400 rather than a 500,
    and a member zipfile itself refuses to read (encrypted, corrupt
    deflate, a file/directory name clash while extracting) is caught the
    same way;
  - path confinement is TWO-LAYERED: every attachment path named inside an
    email record is pre-filtered here (an unsafe or out-of-bounds one is
    silently dropped, so that attachment just reads as missing) and
    `adapters.inbox.LocalInbox.attachment_path` itself refuses to resolve
    outside its root regardless of what a caller hands it - so even a
    record that slipped past this layer, or any OTHER caller of LocalInbox,
    cannot read a file outside the extracted bundle;
  - at most 1000 emails are processed;
  - the model tier gets a hard, per-request budget - 10 attempts and 35
    real seconds - enforced by a contextvar (`adapters.model._BUDGET`) that
    survives the move onto a worker thread (see `_run_pipeline` below), so a
    string of provider FAILURES exhausts the budget exactly as fast as a
    string of successes would; the whole pipeline run happens off the
    asyncio event loop for the same reason a 60s Vercel function duration
    was needed at all - a blocking, possibly-slow run must not block the
    server's event loop.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import time
import zipfile
from typing import Iterator

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import adapters.model as model_mod
from adapters.inbox import LocalInbox
from adapters.ui_rows import build_rows
from core.types import Email

router = APIRouter()

#: 4 MB. Vercel's own request-body limit is 4.5 MB, so this leaves headroom
#: for multipart framing overhead - same margin api/index.py's _EML_MAX_BYTES
#: leaves for an uploaded .eml.
_MAX_UPLOAD_BYTES = 4 * 1024 * 1024

#: Zip-bomb guards, checked against ZipInfo metadata before any byte is
#: extracted, and re-enforced while actually reading (see _read_member_capped).
_MAX_MEMBERS = 5000
_MAX_TOTAL_UNCOMPRESSED_BYTES = 40 * 1024 * 1024
_MAX_MEMBER_BYTES = 8 * 1024 * 1024

#: However large the dataset, at most this many emails are ever processed.
_MAX_EMAILS = 1000

#: The whole request's model budget: at most this many attempts (success OR
#: failure - see adapters/model.py's _BUDGET docstring for why a failure
#: must count too) and at most this many real seconds, whichever comes
#: first. Past either limit every remaining email is forced rules-only.
_MODEL_ATTEMPT_BUDGET = 10
_MODEL_TIME_BUDGET_SECONDS = 35.0

#: The response itself must fit comfortably under Vercel's response limit.
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024

#: Exceptions raised while a zip member is actually being read or written to
#: disk that mean "this member is unreadable", never a bug in our own code:
#: an encrypted entry (RuntimeError), an unsupported compression method
#: (NotImplementedError), corrupt deflate data or a file/directory name
#: clash while extracting (OSError - e.g. writing "attachments/a/b" after
#: "attachments/a" was already written as a plain file), or a bad CRC
#: (zipfile raises BadZipFile/ValueError for these depending on Python
#: version).
_UNREADABLE_MEMBER_EXCEPTIONS = (RuntimeError, NotImplementedError, OSError, ValueError)


def _error(status_code: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": error, "detail": detail})


class DatasetError(Exception):
    """Raised for any 4xx condition found while validating/extracting the
    zip. Caught once, at the route, and turned into the same {error, detail}
    JSON shape every other endpoint in this API uses."""

    def __init__(self, status_code: int, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.error = error
        self.detail = detail


async def _read_capped(upload: UploadFile, max_bytes: int) -> "bytes | None":
    """Read at most max_bytes + 1 bytes. None means "too big" - mirrors
    api/index.py's _read_capped so an oversized upload is never held fully
    in memory just to reject it."""
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        return None
    return data


def _normalise(name: str) -> str:
    return name.replace("\\", "/")


def _is_unsafe_member(name: str) -> bool:
    """True for an absolute path, a Windows drive letter, or any ".."
    segment - the same zip-slip discipline adapters/inbox.py's HttpInbox
    already applies to a server-supplied attachment path, applied here to
    every member of an untrusted upload AND to every attachment path named
    inside an email record (see _safe_attachment_entries)."""
    normalised = _normalise(name)
    if not normalised or not normalised.strip():
        return True
    if normalised.startswith("/"):
        return True
    first_segment = normalised.split("/", 1)[0]
    if len(first_segment) >= 2 and first_segment[1] == ":":
        return True
    parts = [p for p in normalised.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return True
    return False


def _find_dataset_root(names: "Iterator[str]") -> "str | None":
    """The shallowest directory that contains an inbox/ folder with *.json
    files as DIRECT children - so both "bundle.zip -> inbox/.." and
    "bundle.zip -> data_v2/inbox/.." resolve. Returns "" for the zip's own
    root, a prefix ending in "/" for a nested root, or None when no zip
    member matches at all.
    """
    roots: set = set()
    for name in names:
        normalised = _normalise(name)
        if normalised.endswith("/"):
            continue  # a directory entry, not a file
        if normalised.startswith("inbox/"):
            prefix = ""
            rest = normalised[len("inbox/"):]
        else:
            idx = normalised.find("/inbox/")
            if idx == -1:
                continue
            prefix = normalised[: idx + 1]
            rest = normalised[len(prefix) + len("inbox/"):]
        if not rest or "/" in rest:
            continue  # not a direct child of inbox/
        if not rest.lower().endswith(".json"):
            continue
        roots.add(prefix)
    if not roots:
        return None
    # Shallowest first (fewest path segments); alphabetical for determinism
    # on a genuine tie.
    return sorted(roots, key=lambda r: (r.count("/"), r))[0]


def _read_member_capped(zf: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int) -> bytes:
    """Read one member's bytes, aborting once the ACTUAL decompressed size
    (not just what ZipInfo.file_size claims) crosses `cap`. This is the
    second line of defence against a zip whose real content disagrees with
    its own metadata.

    Any of _UNREADABLE_MEMBER_EXCEPTIONS raised while opening or reading the
    member (encrypted, unsupported compression, corrupt data) becomes a
    clean 400 - zipfile's own errors here are not our bug and must never
    reach the client as a 500.
    """
    total = 0
    chunks: "list[bytes]" = []
    try:
        with zf.open(info) as fh:
            while True:
                chunk = fh.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    raise DatasetError(
                        400, "zip_too_large",
                        f"{info.filename!r} exceeds the per-file limit inside the zip.",
                    )
                chunks.append(chunk)
    except DatasetError:
        raise
    except _UNREADABLE_MEMBER_EXCEPTIONS as exc:
        raise DatasetError(
            400, "invalid_zip",
            f"Unreadable member in zip: {info.filename!r} ({type(exc).__name__}).",
        ) from exc
    return b"".join(chunks)


def _write_member_capped(dest: str, content: bytes) -> None:
    """Write one extracted member to disk, turning a filesystem-level clash
    (e.g. "attachments/a" already written as a plain file when
    "attachments/a/b" tries to create a directory called "a") into the same
    clean 400 as an unreadable zip member, never a 500."""
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(content)
    except _UNREADABLE_MEMBER_EXCEPTIONS as exc:
        raise DatasetError(
            400, "invalid_zip",
            f"Could not extract {os.path.basename(dest)!r}: conflicting paths in the zip.",
        ) from exc


def _validate_email_record(rel: str, content: bytes) -> dict:
    """Decode, parse and shape-check one inbox/*.json member BEFORE it is
    ever written to the extracted bundle. Anything wrong here means the
    whole upload is rejected with a clean 400 - never a 500, and never a
    malformed record silently written to disk for LocalInbox to trip over
    later.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DatasetError(400, "invalid_email", f"{rel!r} is not valid UTF-8 text.") from exc
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        raise DatasetError(400, "invalid_email", f"{rel!r} is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise DatasetError(400, "invalid_email", f"{rel!r} must be a JSON object.")
    try:
        Email.from_json(parsed)
    except Exception as exc:
        raise DatasetError(
            400, "invalid_email", f"{rel!r} is not a valid email record: {exc}"
        ) from exc
    return parsed


def _safe_attachment_entries(entries, tmp_dir: str) -> list:
    """Filter an email record's `attachments` list down to entries safe to
    hand to LocalInbox.attachment_path: a plain "attachments/..." relative
    path, no "..", no absolute/drive-letter path, and one whose resolved
    location is actually confined inside THIS dataset's own attachments/
    directory. Anything else is DROPPED, not fatal - the email then simply
    has no attachment there, and the pipeline correctly escalates it as
    missing, the same outcome as a genuinely absent file.

    This is the softer, first layer of the two-layer defence against a
    hostile attachment path: reject before an unsafe-looking reference is
    even written into the Email the pipeline processes. The hard backstop
    is adapters.inbox.LocalInbox.attachment_path itself, which confines to
    its root regardless of what any caller (this one included, and any
    future one) hands it.
    """
    attachments_root = os.path.realpath(os.path.join(tmp_dir, "attachments"))
    safe: "list[str]" = []
    for entry in entries or []:
        if not isinstance(entry, str) or not entry.strip():
            continue
        if _is_unsafe_member(entry):
            continue
        normalised = _normalise(entry)
        if not normalised.startswith("attachments/"):
            continue
        resolved = os.path.realpath(os.path.join(tmp_dir, normalised))
        try:
            confined = os.path.commonpath([attachments_root, resolved]) == attachments_root
        except ValueError:
            confined = False
        if not confined:
            continue
        safe.append(entry)
    return safe


def _extract_dataset(data: bytes, tmp_dir: str) -> None:
    """Validate `data` as a zip and extract ONLY <root>/inbox/*.json and
    <root>/attachments/** into `tmp_dir`. Raises DatasetError on any
    rejection; raises zipfile.BadZipFile if `data` is not a zip at all
    (caller catches that separately, since it is not itself a DatasetError).
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infolist = zf.infolist()

        if len(infolist) > _MAX_MEMBERS:
            raise DatasetError(
                400, "zip_too_large", f"The zip has more than {_MAX_MEMBERS} entries."
            )

        # Zip-slip: check EVERY member, not just the ones we mean to
        # extract - a hostile entry elsewhere in the archive is still a
        # hostile entry.
        for info in infolist:
            if _is_unsafe_member(info.filename):
                raise DatasetError(
                    400, "unsafe_path",
                    f"Refusing an unsafe path inside the zip: {info.filename!r}",
                )

        claimed_total = sum(info.file_size for info in infolist if not info.is_dir())
        if claimed_total > _MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise DatasetError(
                400, "zip_too_large",
                f"The zip's uncompressed content exceeds "
                f"{_MAX_TOTAL_UNCOMPRESSED_BYTES // (1024 * 1024)} MB.",
            )
        for info in infolist:
            if not info.is_dir() and info.file_size > _MAX_MEMBER_BYTES:
                raise DatasetError(
                    400, "zip_too_large",
                    f"{info.filename!r} exceeds the per-file limit inside the zip.",
                )

        names = [info.filename for info in infolist if not info.is_dir()]
        root = _find_dataset_root(iter(names))
        if root is None:
            raise DatasetError(
                400, "no_inbox",
                "No inbox/ folder with .json emails found in the zip",
            )

        inbox_prefix = root + "inbox/"
        attachments_prefix = root + "attachments/"

        json_members: "list[tuple[str, zipfile.ZipInfo]]" = []
        attachment_members: "list[tuple[str, zipfile.ZipInfo]]" = []

        for info in infolist:
            if info.is_dir():
                continue
            name = _normalise(info.filename)
            if name.startswith(inbox_prefix):
                rest = name[len(inbox_prefix):]
                # Only DIRECT children of inbox/ that are .json files - a
                # nested subfolder, or anything else (e.g. a stray
                # ground_truth.json placed inside inbox/ itself), is never
                # extracted or read.
                if rest and "/" not in rest and rest.lower().endswith(".json"):
                    json_members.append((rest, info))
                continue
            if name.startswith(attachments_prefix):
                rest = name[len(attachments_prefix):]
                if rest:
                    attachment_members.append((rest, info))
                continue
            # Anything else in the zip - an answer key, a README, a second
            # copy of the data at another path - is never extracted or read.

        if not json_members:
            raise DatasetError(
                400, "no_inbox",
                "No inbox/ folder with .json emails found in the zip",
            )

        # Cap at 1000 emails, deterministically (same sort order LocalInbox
        # itself iterates in).
        json_members.sort(key=lambda pair: pair[0])
        json_members = json_members[:_MAX_EMAILS]

        inbox_dir = os.path.join(tmp_dir, "inbox")
        attachments_dir = os.path.join(tmp_dir, "attachments")
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(attachments_dir, exist_ok=True)

        grand_total = 0

        for rest, info in json_members:
            content = _read_member_capped(zf, info, _MAX_MEMBER_BYTES)
            grand_total += len(content)
            if grand_total > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise DatasetError(
                    400, "zip_too_large",
                    f"The zip's uncompressed content exceeds "
                    f"{_MAX_TOTAL_UNCOMPRESSED_BYTES // (1024 * 1024)} MB.",
                )
            # Validate and sanitise BEFORE writing - the file that lands in
            # inbox_dir is always the re-serialised, attachment-filtered
            # record, never the raw bytes from the zip.
            record = _validate_email_record(rest, content)
            record["attachments"] = _safe_attachment_entries(record.get("attachments"), tmp_dir)
            sanitized = json.dumps(record).encode("utf-8")
            # rest has no "/" (checked above), so this is always a plain
            # filename directly inside inbox_dir.
            _write_member_capped(os.path.join(inbox_dir, os.path.basename(rest)), sanitized)

        for rest, info in attachment_members:
            content = _read_member_capped(zf, info, _MAX_MEMBER_BYTES)
            grand_total += len(content)
            if grand_total > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise DatasetError(
                    400, "zip_too_large",
                    f"The zip's uncompressed content exceeds "
                    f"{_MAX_TOTAL_UNCOMPRESSED_BYTES // (1024 * 1024)} MB.",
                )
            # rest was validated by _is_unsafe_member above (no "..", not
            # absolute), so this always lands inside attachments_dir.
            dest = os.path.join(attachments_dir, *rest.split("/"))
            _write_member_capped(dest, content)


def _run_pipeline(tmp_dir: str, budget: list) -> dict:
    """The blocking part of the request: build a LocalInbox over the
    already-extracted, already-validated tmp_dir and run every email
    through the pipeline. Runs on a worker thread (see the route below,
    via starlette's run_in_threadpool) so a large or slow dataset never
    blocks the asyncio event loop the rest of the API shares.

    `budget` is set into adapters.model._BUDGET for the DURATION OF THIS
    CALL ONLY, on THIS thread's copy of the context - contextvars set here
    are local to this thread's context, not the caller's, so no explicit
    reset is needed once this function returns and the thread is done with
    the context run_in_threadpool built for it.
    """
    model_mod._BUDGET.set(budget)
    inbox = LocalInbox(tmp_dir)
    return build_rows(inbox, rel=os.path.basename, max_emails=_MAX_EMAILS)


@router.post("/api/process-dataset")
async def process_dataset(file: "UploadFile | None" = File(None)):
    started = time.time()

    if file is None or not file.filename:
        return _error(400, "missing_file", "'file' is required (a .zip)")
    if not file.filename.lower().endswith(".zip"):
        return _error(400, "unsupported_extension", "Only a .zip file is accepted")

    data = await _read_capped(file, _MAX_UPLOAD_BYTES)
    if data is None:
        return _error(
            400, "file_too_large",
            f"file exceeds {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    with tempfile.TemporaryDirectory(prefix="cleardraft_dataset_") as tmp_dir:
        try:
            _extract_dataset(data, tmp_dir)
        except DatasetError as exc:
            return _error(exc.status_code, exc.error, exc.detail)
        except zipfile.BadZipFile:
            return _error(400, "invalid_zip", "The uploaded file is not a valid zip archive.")

        # [attempts_left, deadline] - mutated in place by adapters.model
        # .complete_json on the worker thread; read back below once the
        # run is over. A per-request list, never adapters.model.STATS
        # (process-wide, and racy under concurrent requests), is also what
        # "model_calls" in the response is computed from.
        budget = [_MODEL_ATTEMPT_BUDGET, time.monotonic() + _MODEL_TIME_BUDGET_SECONDS]
        result = await run_in_threadpool(_run_pipeline, tmp_dir, budget)
        model_calls_used = _MODEL_ATTEMPT_BUDGET - budget[0]

    board = result["board"]
    # "exactly as scripts/run_pipeline.py writes them" - Decision.to_submission()'s
    # own shape, rebuilt from the board rows this same run already produced
    # rather than re-run through core a second time.
    submission = {
        row["email_id"]: {
            "category": row["category"],
            "status": row["status"],
            "review_reason": row["review_reason"],
            "has_defect": row["has_defect"],
            "defect_fields": sorted(row["defect_fields"]),
            "decided_by": row["decided_by"],
        }
        for row in board
    }

    display_name = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
    if display_name.lower().endswith(".zip"):
        display_name = display_name[: -len(".zip")]

    payload = {
        "source": {
            "name": display_name,
            "emails": len(board),
            "seconds": round(time.time() - started, 2),
            "model_calls": model_calls_used,
        },
        "board": board,
        "details": result["detail"],
        "stats": result["stats"],
        "submission": submission,
    }

    try:
        body_size = len(json.dumps(payload))
    except (TypeError, ValueError):
        body_size = 0
    if body_size > _MAX_RESPONSE_BYTES:
        return _error(
            413, "response_too_large",
            "The result for this dataset is too large to return (4 MB max).",
        )

    return payload


__all__ = ["router"]
