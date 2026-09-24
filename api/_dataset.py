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
  - at most 1000 emails are processed;
  - the model tier gets a hard budget of at most 30 calls for the whole
    request (see `_gated_model_budget` below) - past the budget, remaining
    emails are forced rules-only rather than left unbounded.
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

from adapters.inbox import LocalInbox
from adapters.ui_rows import build_rows

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

#: The whole request's model-call budget. Past this many calls, every
#: remaining email is forced rules-only (see _gated_model_budget) - an
#: unbounded live pipeline run over an anonymous upload must have a ceiling
#: on how much it can spend, however large the uploaded inbox is.
_MODEL_CALL_BUDGET = 30

#: The response itself must fit comfortably under Vercel's response limit.
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


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
    every member of an untrusted upload."""
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
    its own metadata."""
    total = 0
    chunks: "list[bytes]" = []
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
    return b"".join(chunks)


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
            # rest has no "/" (checked above), so this is always a plain
            # filename directly inside inbox_dir.
            with open(os.path.join(inbox_dir, os.path.basename(rest)), "wb") as fh:
                fh.write(content)

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
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as fh:
                fh.write(content)


def _gated_available(original, start_calls: int, budget: int):
    """Wraps adapters.model.available so the model tier turns itself off,
    for the rest of THIS request only, once STATS.calls has advanced by
    `budget` since `start_calls`. core/classify.py and core/extract.py both
    do `from adapters.model import available` INSIDE the function that uses
    it, at call time - not at module import time - so monkeypatching the
    attribute on the adapters.model module (done by the caller, around this
    function) is enough to gate every call site without touching core/ at
    all. A single email can still push the count slightly past the budget
    (e.g. a classify call plus two extract calls for one email), since the
    gate is only checked before each call - that is an accepted, documented
    slack, not an unbounded one.
    """
    import adapters.model as model_mod

    def gated() -> bool:
        if model_mod.STATS.calls - start_calls >= budget:
            return False
        return original()

    return gated


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

    import adapters.model as model_mod  # local: keep this module import-light for tests that stub it

    try:
        with tempfile.TemporaryDirectory(prefix="cleardraft_dataset_") as tmp_dir:
            try:
                _extract_dataset(data, tmp_dir)
            except zipfile.BadZipFile:
                return _error(400, "invalid_zip", "The uploaded file is not a valid zip archive.")

            inbox = LocalInbox(tmp_dir)

            start_calls = model_mod.STATS.calls
            original_available = model_mod.available
            model_mod.available = _gated_available(original_available, start_calls, _MODEL_CALL_BUDGET)
            try:
                result = build_rows(inbox, rel=os.path.basename, max_emails=_MAX_EMAILS)
            finally:
                model_mod.available = original_available
            model_calls_used = model_mod.STATS.calls - start_calls
    except DatasetError as exc:
        return _error(exc.status_code, exc.error, exc.detail)

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
