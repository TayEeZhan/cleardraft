"""The held-out set must keep the cases that exposed our assumptions."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "data" / "challenge" / "docs"


def _gold() -> dict:
    return json.loads((DOCS / "gold.json").read_text(encoding="utf-8"))


def test_adversarial_cases_are_present() -> None:
    gold = _gold()
    required = {
        "unit_mt",
        "unit_lbs",
        "unit_unsupported",
        "compound_count",
        "suffix_abbreviation",
        "token_reorder",
        "country_suffix",
        "punctuation",
        "side_by_side",
        "placement_guard",
    }
    assert required <= set(gold)
    assert len(gold) >= 12


def test_every_gold_pair_has_both_documents_and_seven_fields() -> None:
    required_fields = {
        "shipper", "consignee", "notify_party", "port_of_loading",
        "port_of_discharge", "container_count", "gross_weight_kg",
    }
    for name, record in _gold().items():
        files = record.get("files", {})
        assert (DOCS / files.get("si", f"{name}_SI.txt")).is_file(), name
        assert (DOCS / files.get("bl", f"{name}_BL.txt")).is_file(), name
        assert set(record["si"]) == required_fields, name
        assert set(record["bl"]) == required_fields, name


def test_gold_names_the_expected_safety_outcomes() -> None:
    gold = _gold()
    assert gold["unit_unsupported"]["undecidable_fields"] == ["gross_weight_kg"]
    assert gold["compound_count"]["undecidable_fields"] == ["container_count"]
    assert gold["suffix_abbreviation"]["variance"] == {"shipper": "suffix_abbreviation"}
    assert gold["token_reorder"]["variance"] == {"shipper": "token_reorder"}
    assert gold["country_suffix"]["variance"] == {"shipper": "country_suffix"}
    assert gold["side_by_side"]["files"]["bl"].endswith(".pdf")


def test_multi_draft_fixture_requires_an_explicit_selection_policy() -> None:
    fixture = ROOT / "data" / "challenge" / "multi_draft"
    email = json.loads((fixture / "email.json").read_text(encoding="utf-8"))
    assert len(email["attachments"]) == 3
    assert sum("BL" in name for name in email["attachments"]) == 2
    assert all((fixture / name).is_file() for name in email["attachments"])
