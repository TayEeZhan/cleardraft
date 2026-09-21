"""Tests for adapters/eml.py and the POST /api/process-email endpoint.

The model tier is OFF for every test (tests/conftest.py sets
CLEARDRAFT_USE_MODEL=0) so nothing here makes a network call.
"""
from __future__ import annotations

import email.message
import email.utils
import json
import os

import pytest
from fastapi.testclient import TestClient

from adapters.eml import EmlError, parse_eml, safe_filename
from api.index import app

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "web", "samples", "eml")
DATA_JSON_PATH = os.path.join(ROOT, "web", "public", "data.json")

client = TestClient(app)


# ---------------------------------------------------------------------------
# adapters/eml.py: parse_eml
# ---------------------------------------------------------------------------
def _make_eml(**overrides) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = overrides.get("sender", "docs@x.sg")
    msg["Subject"] = overrides.get("subject", "Test subject")
    if "date" in overrides:
        msg["Date"] = overrides["date"]
    msg.set_content(overrides.get("body", "Hello there.\nLine two."))
    return bytes(msg)


def test_parse_plain_text_email():
    raw = _make_eml(
        sender="Docs Team <docs@x.sg>",
        subject="Please compare",
        date="Mon, 21 Sep 2026 10:00:00 +0000",
        body="Hi,\n\nPlease see attached.\n",
    )
    parsed = parse_eml(raw)
    assert parsed.sender == "docs@x.sg"
    assert parsed.subject == "Please compare"
    assert "Please see attached." in parsed.body
    assert parsed.date == "Mon, 21 Sep 2026 10:00:00 +0000"
    assert parsed.attachments == ()


def test_parse_multipart_with_two_attachments():
    msg = email.message.EmailMessage()
    msg["From"] = "docs@x.sg"
    msg["Subject"] = "SI and BL attached"
    msg.set_content("Please see attached.")
    msg.add_attachment(
        b"si content", maintype="text", subtype="plain", filename="email_004_SI.txt"
    )
    msg.add_attachment(
        b"bl content", maintype="text", subtype="plain", filename="email_004_BL.txt"
    )
    parsed = parse_eml(bytes(msg))
    assert len(parsed.attachments) == 2
    names = [name for name, _ in parsed.attachments]
    assert "email_004_SI.txt" in names
    assert "email_004_BL.txt" in names
    contents = dict(parsed.attachments)
    assert contents["email_004_SI.txt"] == b"si content"
    assert contents["email_004_BL.txt"] == b"bl content"


def test_parse_html_only_body_becomes_readable_text():
    msg = email.message.EmailMessage()
    msg["From"] = "docs@x.sg"
    msg["Subject"] = "HTML body"
    msg.set_content(
        "<html><body><p>Hello &amp; welcome</p><p>Second line</p><br>Bye</body></html>",
        subtype="html",
    )
    parsed = parse_eml(bytes(msg))
    assert "<" not in parsed.body
    assert ">" not in parsed.body
    assert "Hello & welcome" in parsed.body
    assert "Second line" in parsed.body
    assert "Bye" in parsed.body


def test_parse_non_email_bytes_raises_eml_error():
    with pytest.raises(EmlError):
        parse_eml(b"not an email at all, just garbage bytes 12345")


def test_parse_empty_bytes_raises_eml_error():
    with pytest.raises(EmlError):
        parse_eml(b"")


# ---------------------------------------------------------------------------
# safe_filename
# ---------------------------------------------------------------------------
def test_safe_filename_defeats_path_traversal():
    result = safe_filename("../../evil.txt", 0)
    assert "/" not in result
    assert "\\" not in result
    assert ".." not in result
    assert result.endswith("evil.txt")


def test_safe_filename_defeats_windows_absolute_path():
    result = safe_filename("C:\\x\\y.txt", 1)
    assert "\\" not in result
    assert ":" not in result
    assert result.endswith("y.txt")


def test_safe_filename_defeats_empty_name():
    result = safe_filename("", 2)
    assert result
    assert result.startswith("2_")


def test_safe_filename_keeps_si_bl_suffix_for_pipeline_fallback():
    result = safe_filename("email_004_SI.txt", 0)
    assert "_SI." in result
    assert result == "0_email_004_SI.txt"

    result_bl = safe_filename("email_004_BL.txt", 1)
    assert "_BL." in result_bl


# ---------------------------------------------------------------------------
# POST /api/process-email
# ---------------------------------------------------------------------------
def _sample_bytes(name: str) -> bytes:
    with open(os.path.join(SAMPLES, name), "rb") as fh:
        return fh.read()


def _upload(name: str, data: "bytes | None" = None):
    payload = data if data is not None else _sample_bytes(name)
    return client.post(
        "/api/process-email",
        files={"eml": (name, payload, "message/rfc822")},
    )


def test_email_004_reports_mismatch_with_expected_defect_fields():
    resp = _upload("01_mismatch_email_004.eml")
    assert resp.status_code == 200
    body = resp.json()

    board = body["board"]
    detail = body["detail"]

    assert board["status"] == "MISMATCH"
    assert board["category"] == "BL_COMPARISON"
    # Order as it actually appears in web/public/data.json for email_004.
    assert board["defect_fields"] == ["consignee", "notify_party"]
    assert board["email_id"].startswith("up_")

    assert detail["uploaded"] is True
    assert detail["filename"] == "01_mismatch_email_004.eml"
    assert detail["status"] == "MISMATCH"
    assert detail["defect_fields"] == ["consignee", "notify_party"]
    assert len(detail["comparisons"]) == 7
    assert detail["documents"]["si"] is not None
    assert detail["documents"]["bl"] is not None
    assert detail["reply_draft"]
    assert detail["recheck"] is None

    assert body["model"]["calls"] == 0
    assert body["model"]["available"] is False


def test_upload_id_is_stable_across_two_uploads():
    data = _sample_bytes("01_mismatch_email_004.eml")
    first = _upload("01_mismatch_email_004.eml", data).json()
    second = _upload("01_mismatch_email_004.eml", data).json()
    assert first["board"]["email_id"] == second["board"]["email_id"]
    assert first["board"]["email_id"].startswith("up_")


def test_wrong_extension_is_400():
    resp = client.post(
        "/api/process-email",
        files={"eml": ("not-an-eml.txt", b"whatever", "text/plain")},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "unsupported_extension"


def test_missing_file_is_400():
    """No `eml` file and no pasted `body` either: /api/process-email now has
    a second request shape (paste-an-email mode, see api/index.py), so an
    empty request falls through to that mode's own "nothing to process"
    error rather than treating the absent `eml` field as a bad extension."""
    resp = client.post("/api/process-email", files={})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "missing_email"


def test_oversized_file_is_400():
    oversized = b"a" * (4 * 1024 * 1024 + 1)
    resp = client.post(
        "/api/process-email",
        files={"eml": ("big.eml", oversized, "message/rfc822")},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "file_too_large"


def test_garbage_bytes_named_eml_is_400_not_an_email():
    resp = client.post(
        "/api/process-email",
        files={"eml": ("x.eml", b"totally not an email, just noise", "message/rfc822")},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "not_an_email"


# ---------------------------------------------------------------------------
# Consistency: the 5 dataset-derived samples must match web/public/data.json,
# the same source of truth the offline UI reads.
# ---------------------------------------------------------------------------
_DATASET_SAMPLES = (
    ("01_mismatch_email_004.eml", "email_004"),
    ("02_mismatch_pdf_email_313.eml", "email_313"),
    ("03_cleared_email_001.eml", "email_001"),
    ("04_needs_review_email_208.eml", "email_208"),
    ("05_nonbl_invoice_email_002.eml", "email_002"),
)


def _board_row(email_id: str) -> dict:
    with open(DATA_JSON_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    for row in data["board"]:
        if row["email_id"] == email_id:
            return row
    raise AssertionError(f"{email_id!r} not found in web/public/data.json board")


@pytest.mark.parametrize("filename,email_id", _DATASET_SAMPLES)
def test_dataset_sample_matches_data_json(filename, email_id):
    expected = _board_row(email_id)
    resp = _upload(filename)
    assert resp.status_code == 200
    board = resp.json()["board"]
    assert board["status"] == expected["status"]
    assert board["category"] == expected["category"]
    assert board["defect_fields"] == expected["defect_fields"]
