"""Tests for the live-check HTTP API (api/index.py).

The model tier is OFF for every test (tests/conftest.py sets
CLEARDRAFT_USE_MODEL=0) so nothing here makes a network call.
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

from api.index import _unit_fields, app
from core.types import FieldComparison, FieldValue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTACHMENTS = os.path.join(ROOT, "data", "attachments")

client = TestClient(app)


def _attachment(name: str) -> bytes:
    with open(os.path.join(ATTACHMENTS, name), "rb") as fh:
        return fh.read()


def _upload(si_name: str, bl_name: str, *, si_bytes=None, bl_bytes=None, **form):
    files = {
        "si": (si_name, si_bytes if si_bytes is not None else _attachment(si_name), "application/octet-stream"),
        "bl": (bl_name, bl_bytes if bl_bytes is not None else _attachment(bl_name), "application/octet-stream"),
    }
    return client.post("/api/check", files=files, data=form)


def test_health_returns_ok():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["model_available"], bool)
    assert isinstance(body["parsers"], list)
    assert isinstance(body["missing_parsers"], dict)
    assert isinstance(body["commit"], str) and len(body["commit"]) <= 7
    assert body["model"] == "claude-haiku-4-5"
    # tests/conftest.py sets CLEARDRAFT_USE_MODEL=0 for the whole suite.
    assert body["model_switch"] == "off"


def test_mismatching_pair_reports_defects():
    resp = _upload("email_004_SI.txt", "email_004_BL.txt")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "MISMATCH"
    assert body["defect_fields"] == ["consignee", "notify_party"]
    assert len(body["comparisons"]) == 7
    assert body["reply_draft"]
    assert body["category"] == "BL_COMPARISON"
    assert body["decided_by"] == "rule"
    assert body["documents"]["si"]["name"] == "email_004_SI.txt"
    assert body["documents"]["bl"]["name"] == "email_004_BL.txt"
    assert body["documents"]["si"]["readable"] is True
    assert body["documents"]["bl"]["readable"] is True
    assert "subject" in body
    assert body["from"] == ""
    assert "model" in body and body["model"]["available"] is False


def test_clean_pair_reports_ok():
    resp = _upload("email_001_SI.txt", "email_001_BL.txt")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "OK"
    assert body["defect_fields"] == []
    assert len(body["comparisons"]) == 7


def test_corrupt_pdf_is_needs_review_not_a_500():
    resp = _upload("email_001_SI.txt", "email_511_BL.pdf")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "NEEDS_REVIEW"
    assert body["review_reason"] == "unreadable"
    assert body["documents"]["bl"]["readable"] is False
    assert body["comparisons"] == []


def test_missing_bl_is_400():
    files = {"si": ("email_001_SI.txt", _attachment("email_001_SI.txt"), "text/plain")}
    resp = client.post("/api/check", files=files)
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body and "detail" in body


def test_missing_si_is_400():
    files = {"bl": ("email_001_BL.txt", _attachment("email_001_BL.txt"), "text/plain")}
    resp = client.post("/api/check", files=files)
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body and "detail" in body


def test_disallowed_extension_is_400():
    resp = _upload(
        "malware.exe", "email_001_BL.txt", si_bytes=b"MZ-not-a-real-document"
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body and "detail" in body


def test_oversized_file_is_400():
    oversized = b"a" * (2 * 1024 * 1024 + 1)
    resp = _upload("big.txt", "email_001_BL.txt", si_bytes=oversized)
    assert resp.status_code == 400
    body = resp.json()
    assert "error" in body and "detail" in body


def test_exactly_two_mb_file_is_accepted():
    """The boundary: 2 MB exactly must NOT be rejected as oversized."""
    exactly_cap = b"a" * (2 * 1024 * 1024)
    resp = _upload("boundary.txt", "email_001_BL.txt", si_bytes=exactly_cap)
    assert resp.status_code == 200


def test_malicious_filename_is_not_used_as_a_path():
    """A path-traversal filename must never escape the request's tempdir.

    The extension (.txt) is still valid, so this should succeed like any
    other .txt upload - the request must not try to write to
    ../../evil.txt anywhere on disk.
    """
    before = set()
    parent_of_repo = os.path.dirname(ROOT)
    for root, _dirs, _files in os.walk(parent_of_repo):
        # Cheap guard against walking something huge; we only need to know
        # this exact file doesn't exist before and after.
        break
    evil_path = os.path.join(parent_of_repo, "evil.txt")
    assert not os.path.exists(evil_path)

    resp = _upload(
        "../../evil.txt",
        "email_001_BL.txt",
        si_bytes=_attachment("email_001_SI.txt"),
    )
    assert resp.status_code == 200
    assert not os.path.exists(evil_path)


def test_subject_and_body_trigger_email_reading():
    resp = _upload(
        "email_004_SI.txt",
        "email_004_BL.txt",
        subject="Please compare the SI and draft BL for OC-123 and confirm",
        body="Kindly check and revert.",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email_reading"] is not None
    assert body["email_reading"]["category"] in (
        "BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM",
    )
    assert body["subject"] == "Please compare the SI and draft BL for OC-123 and confirm"
    # The decision itself is always forced to a compare, regardless of what
    # the classifier made of the subject/body.
    assert body["category"] == "BL_COMPARISON"
    assert body["decided_by"] == "rule"


def test_no_subject_or_body_means_no_email_reading():
    resp = _upload("email_004_SI.txt", "email_004_BL.txt")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email_reading"] is None


def test_emails_endpoint_is_not_implemented():
    """The web UI probes /api/emails; it must not be answered by this API."""
    resp = client.get("/api/emails")
    assert resp.status_code == 404


def _weight_fv(value: str) -> FieldValue:
    return FieldValue(value=value, raw=value, line_no=1, label="Gross Wt (kgs)")


def test_unit_fields_flags_same_weight_different_unit():
    """22 MT and 22,000 KG are the same weight, written in different units -
    _unit_fields must surface that as unit_note/unit_kg (see core/units.py's
    describe_unit_difference/parse_weight, which this wraps)."""
    c = FieldComparison(
        field="gross_weight_kg",
        si=_weight_fv("22 MT"),
        bl=_weight_fv("22,000 KG"),
        si_norm=22000,
        bl_norm=22000,
        matched=True,
    )
    fields = _unit_fields(c)
    assert fields["unit_note"] == "MT vs KG"
    assert fields["unit_kg"] == 22000


def test_unit_fields_none_for_ordinary_same_unit_match():
    c = FieldComparison(
        field="gross_weight_kg",
        si=_weight_fv("21,577 KG"),
        bl=_weight_fv("21,577 KG"),
        si_norm=21577,
        bl_norm=21577,
        matched=True,
    )
    fields = _unit_fields(c)
    assert fields["unit_note"] is None
    assert fields["unit_kg"] is None


def test_unit_fields_none_for_non_weight_field():
    c = FieldComparison(
        field="container_count",
        si=_weight_fv("22 MT"),
        bl=_weight_fv("22,000 KG"),
        si_norm=22000,
        bl_norm=22000,
        matched=True,
    )
    fields = _unit_fields(c)
    assert fields["unit_note"] is None
    assert fields["unit_kg"] is None
