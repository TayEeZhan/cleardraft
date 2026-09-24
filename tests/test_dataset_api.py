"""Tests for POST /api/process-dataset (api/_dataset.py).

Hermetic: no network, no model calls (tests/conftest.py sets
CLEARDRAFT_USE_MODEL=0 for the whole suite). Every zip here is built in
memory from a handful of this repo's own data/inbox + data/attachments
files, so these tests never depend on an external fixture file.
"""
from __future__ import annotations

import io
import json
import os
import zipfile

from fastapi.testclient import TestClient

from api.index import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
INBOX_DIR = os.path.join(DATA, "inbox")
ATTACHMENTS_DIR = os.path.join(DATA, "attachments")

client = TestClient(app)


def _inbox_json(email_id: str) -> dict:
    with open(os.path.join(INBOX_DIR, f"{email_id}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _attachment_bytes(rel: str) -> bytes:
    with open(os.path.join(DATA, rel), "rb") as fh:
        return fh.read()


def _build_zip(email_ids, *, root="", extra=None) -> bytes:
    """A zip containing <root>inbox/<id>.json for every id in email_ids, plus
    every attachment those emails reference, at <root>attachments/<name>.
    `extra` is a list of (arcname, bytes) written as-is, for zip-slip /
    ground-truth / nested-root test fixtures."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen_attachments = set()
        for email_id in email_ids:
            record = _inbox_json(email_id)
            zf.writestr(f"{root}inbox/{email_id}.json", json.dumps(record))
            for rel in record.get("attachments") or []:
                if rel in seen_attachments:
                    continue
                seen_attachments.add(rel)
                # rel is already "attachments/<name>" in the JSON.
                arcname = root + rel
                zf.write(os.path.join(DATA, rel), arcname=arcname)
        for arcname, content in extra or []:
            zf.writestr(arcname, content)
    return buf.getvalue()


def _post(data: bytes, filename: str = "bundle.zip"):
    return client.post(
        "/api/process-dataset",
        files={"file": (filename, io.BytesIO(data), "application/zip")},
    )


# ---------------------------------------------------------------------------
# Happy path: a handful of emails covering every attachment format, plus one
# with no attachments at all.
# ---------------------------------------------------------------------------
def test_txt_xlsx_docx_pdf_and_no_attachment_mix():
    # email_001: SI/BL .txt, clean pair (OK)
    # email_004: SI/BL .txt, a real mismatch (consignee, notify_party)
    # email_055: SI .xlsx / BL .docx (a mixed-format pair)
    # email_059: SI/BL .pdf
    # email_002: no attachments at all (INVOICE_QUERY)
    ids = ["email_001", "email_004", "email_055", "email_059", "email_002"]
    data = _build_zip(ids)
    resp = _post(data, "mixed.zip")
    assert resp.status_code == 200
    body = resp.json()

    assert body["source"]["name"] == "mixed"
    assert body["source"]["emails"] == len(ids)
    assert isinstance(body["source"]["seconds"], float)
    assert body["source"]["model_calls"] == 0

    board_by_id = {row["email_id"]: row for row in body["board"]}
    assert set(board_by_id) == set(ids)

    assert board_by_id["email_001"]["status"] == "OK"
    assert board_by_id["email_004"]["status"] == "MISMATCH"
    assert board_by_id["email_004"]["defect_fields"] == ["consignee", "notify_party"]
    assert board_by_id["email_002"]["category"] == "INVOICE_QUERY"
    assert board_by_id["email_002"]["attachment_count"] == 0

    # Same row/detail shapes as web/public/data.json.
    detail = body["details"]["email_004"]
    assert detail["comparisons"] and len(detail["comparisons"]) == 7
    assert detail["documents"]["si"]["readable"] is True
    assert detail["documents"]["bl"]["readable"] is True

    xlsx_docx_detail = body["details"]["email_055"]
    assert xlsx_docx_detail["documents"]["si"]["readable"] is True
    assert xlsx_docx_detail["documents"]["bl"]["readable"] is True

    pdf_detail = body["details"]["email_059"]
    assert pdf_detail["documents"]["si"]["readable"] is True
    assert pdf_detail["documents"]["bl"]["readable"] is True

    # The submission block matches scripts/run_pipeline.py's own record
    # shape exactly - one entry per email, the same six keys.
    assert set(body["submission"]) == set(ids)
    for email_id, record in body["submission"].items():
        assert set(record) == {
            "category", "status", "review_reason", "has_defect",
            "defect_fields", "decided_by",
        }
        assert record["category"] == board_by_id[email_id]["category"]
        assert record["status"] == board_by_id[email_id]["status"]
        assert record["defect_fields"] == sorted(board_by_id[email_id]["defect_fields"])

    assert body["stats"]["totals"]["emails"] == len(ids)


def test_nested_dataset_root_is_found():
    """bundle.zip -> data_v2/inbox/..., data_v2/attachments/... must work
    exactly like a zip root."""
    data = _build_zip(["email_001", "email_004"], root="data_v2/")
    resp = _post(data)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["emails"] == 2
    assert set(body["board"][i]["email_id"] for i in range(2)) == {"email_001", "email_004"}


def test_ground_truth_json_is_never_read():
    """An answer key sitting in the zip, even with invalid JSON content,
    must not be extracted, parsed, or otherwise stop processing."""
    data = _build_zip(
        ["email_001", "email_004"],
        extra=[("ground_truth.json", "this is not valid json {{{ at all")],
    )
    resp = _post(data)
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"]["emails"] == 2
    # Only the two real emails made it onto the board - the answer key did
    # not become a third (broken) entry.
    assert {row["email_id"] for row in body["board"]} == {"email_001", "email_004"}


def test_zip_slip_member_is_rejected():
    data = _build_zip(["email_001"], extra=[("../../evil.txt", "haha")])
    resp = _post(data)
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "unsafe_path"


def test_absolute_path_member_is_rejected():
    data = _build_zip(["email_001"], extra=[("/etc/passwd", "haha")])
    resp = _post(data)
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsafe_path"


def test_windows_drive_letter_member_is_rejected():
    data = _build_zip(["email_001"], extra=[(r"C:\evil.txt", "haha")])
    resp = _post(data)
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsafe_path"


def test_oversized_upload_is_rejected():
    # Past the 4 MB request-body cap - never even opened as a zip.
    oversized = b"PK" + os.urandom(4 * 1024 * 1024 + 1)
    resp = _post(oversized, "big.zip")
    assert resp.status_code == 400
    assert resp.json()["error"] == "file_too_large"


def test_non_zip_upload_is_rejected():
    resp = _post(b"this is definitely not a zip file", "notazip.zip")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_zip"


def test_wrong_extension_is_rejected():
    resp = client.post(
        "/api/process-dataset",
        files={"file": ("bundle.tar", io.BytesIO(b"whatever"), "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_extension"


def test_missing_file_is_400():
    resp = client.post("/api/process-dataset")
    assert resp.status_code == 400
    assert resp.json()["error"] == "missing_file"


def test_missing_inbox_folder_is_rejected_with_clear_message():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("attachments/whatever.txt", "hi")
        zf.writestr("readme.txt", "no inbox here")
    resp = _post(buf.getvalue())
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "no_inbox"
    assert "inbox/" in body["detail"]
    assert ".json" in body["detail"]


def test_zip_with_only_ground_truth_and_no_inbox_is_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ground_truth.json", json.dumps({"email_001": {}}))
    resp = _post(buf.getvalue())
    assert resp.status_code == 400
    assert resp.json()["error"] == "no_inbox"


def test_member_count_cap_is_enforced():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        record = _inbox_json("email_001")
        zf.writestr("inbox/email_001.json", json.dumps(record))
        for rel in record.get("attachments") or []:
            zf.write(os.path.join(DATA, rel), arcname=rel)
        for i in range(5001):
            zf.writestr(f"filler/junk_{i}.txt", "x")
    resp = _post(buf.getvalue(), "huge.zip")
    assert resp.status_code == 400
    assert resp.json()["error"] == "zip_too_large"


# ---------------------------------------------------------------------------
# Full corpus: every email in data/, zipped, posted, and checked against the
# published snapshot (web/public/data.json) for the same run in this same
# environment - not a hardcoded number, since the sample corpus's own
# published count can drift with the checked-in snapshot (see
# docs/PARSER_REVIEW.md for a known, environment-dependent PDF-parsing
# case). Runs in a few seconds - no separate "slow" marker needed.
# ---------------------------------------------------------------------------
def test_full_corpus_matches_batch_pipeline():
    ids = sorted(name[:-5] for name in os.listdir(INBOX_DIR) if name.endswith(".json"))
    assert len(ids) == 520

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(os.listdir(INBOX_DIR)):
            zf.write(os.path.join(INBOX_DIR, name), arcname=f"inbox/{name}")
        for root, _dirs, files in os.walk(ATTACHMENTS_DIR):
            for f in files:
                full = os.path.join(root, f)
                arcname = "attachments/" + os.path.relpath(full, ATTACHMENTS_DIR).replace(os.sep, "/")
                zf.write(full, arcname=arcname)

    resp = _post(buf.getvalue(), "full_corpus.zip")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["board"]) == 520
    assert body["source"]["emails"] == 520

    live_mismatches = sum(1 for row in body["board"] if row["status"] == "MISMATCH")

    # Run the same pipeline the same way scripts/export_ui_data.py does,
    # in-process, over the same data/ folder, and compare against THAT -
    # the one invariant that must hold regardless of what any given
    # environment's document parsers do with a specific PDF.
    from adapters.inbox import LocalInbox
    from adapters.ui_rows import build_rows

    reference = build_rows(LocalInbox(DATA), rel=os.path.basename)
    reference_mismatches = sum(1 for row in reference["board"] if row["status"] == "MISMATCH")

    assert live_mismatches == reference_mismatches
