"""Tests for "Fix a value": POST /api/recheck-field (api/_recheck.py) and its
pure core, core.compare.compare_values. No account, no store - this endpoint
is stateless, so no memory_store fixture is needed here (unlike
tests/test_equivalences_api.py / tests/test_feedback_api.py).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.index import app
from core.compare import compare_values

client = TestClient(app)


def _recheck(field: str, si_value: str, bl_value: str):
    return client.post(
        "/api/recheck-field",
        json={"field": field, "si_value": si_value, "bl_value": bl_value},
    )


# ---------------------------------------------------------------------------
# core.compare.compare_values - one case per field type
# ---------------------------------------------------------------------------
def test_entity_field_match_after_punctuation_cleanup():
    result = compare_values("consignee", "MOORIM SP CO., LTD", "MOORIM SP CO LTD")
    assert result["status"] == "match"
    assert result["si_normalised"] == result["bl_normalised"] == "MOORIM SP CO LTD"


def test_entity_field_mismatch_is_a_real_defect():
    result = compare_values("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA")
    assert result["status"] == "mismatch"
    assert result["si_normalised"] == "EAST BRIGHT FZ-LLC"
    assert result["bl_normalised"] == "UAB NOVAKOPA"


def test_shipper_field():
    result = compare_values("shipper", "APRIL FAR EAST (M) SDN BHD", "APRIL FAR EAST (M) SDN BHD")
    assert result["status"] == "match"


def test_notify_party_field():
    result = compare_values("notify_party", "EAST BRIGHT FZ-LLC", "EAST BRIGHT FZ-LLC")
    assert result["status"] == "match"


def test_port_field_strips_trailing_locode():
    result = compare_values("port_of_loading", "NANTONG, CHINA (CNNTG)", "NANTONG, CHINA")
    assert result["status"] == "match"


def test_port_field_mismatch():
    result = compare_values("port_of_discharge", "KARACHI, PAKISTAN (PKKHI)", "SINGAPORE (SGSIN)")
    assert result["status"] == "mismatch"


def test_container_count_field_match():
    result = compare_values("container_count", "6 x 40'HC", "6 CONTAINERS")
    assert result["status"] == "match"
    assert result["si_normalised"] == result["bl_normalised"] == 6


def test_container_count_field_mismatch():
    result = compare_values("container_count", "6 x 40'HC", "5 x 40'HC")
    assert result["status"] == "mismatch"


def test_container_count_fails_closed_on_size_before_multiplier():
    """"40' x 2" is ambiguous about which number is the quantity - never
    guessed at, always undecidable (core/normalise.py:_SIZE_BEFORE_MULTIPLIER)."""
    result = compare_values("container_count", "40' x 2", "2 x 40'HC")
    assert result["status"] == "undecidable"
    assert result["si_normalised"] is None


def test_container_count_fails_closed_on_mixed_equipment():
    result = compare_values("container_count", "2 x 40HC + 1 x 20GP", "2 x 40HC + 1 x 20GP")
    assert result["status"] == "undecidable"


def test_weight_field_match():
    result = compare_values("gross_weight_kg", "131,058 KG", "131,058 KG")
    assert result["status"] == "match"
    assert result["si_normalised"] == result["bl_normalised"] == 131058


def test_weight_field_unit_conversion_match_with_note():
    """Same weight, different unit: a match, with a note explaining why the
    two written numbers differ (core.units.describe_unit_difference)."""
    result = compare_values("gross_weight_kg", "22 MT", "22,000 KG")
    assert result["status"] == "match"
    assert result["note"] == "MT vs KG"


def test_weight_field_mismatch():
    result = compare_values("gross_weight_kg", "131,058 KG", "132,058 KG")
    assert result["status"] == "mismatch"
    assert result["note"] is None


def test_weight_field_unsupported_unit_is_undecidable():
    result = compare_values("gross_weight_kg", "22 QTL", "22 QTL")
    assert result["status"] == "undecidable"


def test_blank_side_is_undecidable():
    result = compare_values("shipper", "", "APRIL FAR EAST (M) SDN BHD")
    assert result["status"] == "undecidable"
    assert result["si_normalised"] is None


def test_unknown_field_never_raises():
    result = compare_values("not_a_real_field", "x", "y")
    assert result["status"] == "undecidable"


# ---------------------------------------------------------------------------
# POST /api/recheck-field - transport layer
# ---------------------------------------------------------------------------
def test_endpoint_match():
    resp = _recheck("consignee", "EAST BRIGHT FZ-LLC", "EAST BRIGHT FZ-LLC")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "field": "consignee",
        "status": "match",
        "si_normalised": "EAST BRIGHT FZ-LLC",
        "bl_normalised": "EAST BRIGHT FZ-LLC",
        "note": None,
    }


def test_endpoint_mismatch():
    resp = _recheck("consignee", "EAST BRIGHT FZ-LLC", "UAB NOVAKOPA")
    assert resp.status_code == 200
    assert resp.json()["status"] == "mismatch"


def test_endpoint_weight_unit_note():
    resp = _recheck("gross_weight_kg", "22 MT", "22,000 KG")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "match"
    assert body["note"] == "MT vs KG"


def test_endpoint_container_fails_closed():
    resp = _recheck("container_count", "40' x 2", "2 x 40'HC")
    assert resp.status_code == 200
    assert resp.json()["status"] == "undecidable"


def test_endpoint_rejects_unknown_field():
    resp = _recheck("bill_of_lading_number", "x", "y")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_field"


@pytest.mark.parametrize("field", ["", "SHIPPER", "consignee "])
def test_endpoint_rejects_field_variants_not_in_compare_fields(field):
    resp = _recheck(field, "x", "y")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_field"


def test_endpoint_rejects_oversized_values():
    resp = _recheck("shipper", "x" * 501, "y")
    assert resp.status_code == 400
    assert resp.json()["error"] == "value_too_long"

    resp = _recheck("shipper", "x", "y" * 501)
    assert resp.status_code == 400
    assert resp.json()["error"] == "value_too_long"


def test_endpoint_accepts_value_at_the_cap():
    resp = _recheck("shipper", "x" * 500, "x" * 500)
    assert resp.status_code == 200
    assert resp.json()["status"] == "match"


def test_endpoint_missing_body_fields_default_to_blank():
    resp = client.post("/api/recheck-field", json={"field": "shipper"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "undecidable"


# ---------------------------------------------------------------------------
# `hint` - additive, mismatch-only (core.variance.explain_difference).
# Feature: "Try to fool it" (#/check, Type values mode).
# ---------------------------------------------------------------------------
def test_endpoint_mismatch_includes_hint_for_suffix_abbreviation():
    resp = _recheck("shipper", "Ocean Paper Co Ltd", "OCEAN PAPER COMPANY LIMITED")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "mismatch"
    assert body["hint"] == {
        "kind": "suffix_abbreviation",
        "label": "Possible company-suffix abbreviation",
    }


def test_endpoint_mismatch_includes_hint_for_port_alias():
    resp = _recheck("port_of_loading", "PORT KLANG", "PORT KELANG")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "mismatch"
    assert body["hint"]["kind"] == "port_alias"


def test_endpoint_mismatch_without_a_known_pattern_has_hint_none():
    """Two genuinely different ports (Dubai / Jebel Ali) must never be
    handed a same-port hint just because both are in the UAE."""
    resp = _recheck("port_of_discharge", "DUBAI", "JEBEL ALI")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "mismatch"
    assert body["hint"] is None


def test_endpoint_match_body_has_no_hint_key():
    """Exact body equality, same as test_endpoint_match above: adding `hint`
    must never touch a match response's shape."""
    resp = _recheck("consignee", "EAST BRIGHT FZ-LLC", "EAST BRIGHT FZ-LLC")
    assert resp.status_code == 200
    body = resp.json()
    assert "hint" not in body
    assert body == {
        "field": "consignee",
        "status": "match",
        "si_normalised": "EAST BRIGHT FZ-LLC",
        "bl_normalised": "EAST BRIGHT FZ-LLC",
        "note": None,
    }


def test_endpoint_undecidable_body_has_no_hint_key():
    resp = _recheck("container_count", "40' x 2", "2 x 40'HC")
    assert resp.status_code == 200
    assert "hint" not in resp.json()
