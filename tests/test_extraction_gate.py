"""The model tier, proven without an API key.

OWNER: Sheng Kuan.

"The AI cannot invent a consignee" is the central claim of this product. It
is true only because every model-supplied value must appear verbatim in the
document text. These tests fake the model so the guarantee is demonstrable
today, with no key, no network and no cost - and so it stays true after
someone edits _fill_missing_with_model().

test_classify.py covers the same two-tier behaviour for classification. This
file covers extraction, which is where an invented value would become a
reported discrepancy on a real Bill of Lading.

Running the real model against the real provider is still required before the
demo.
"""
from __future__ import annotations

import adapters.model as model_adapter
import pytest

from core.extract import extract, verify_against_source

#: An SI whose consignee row is absent, so the rule tier leaves that field
#: missing and the model tier is asked for it. The company name appears in the
#: body as an unlabelled, indented line - present in doc.text, invisible to
#: the label parser.
DOCUMENT = """SHIPPING INSTRUCTION
========================================

Shipper: APRIL FAR EAST (M) SDN BHD
  TOWER 2, AVENUE 5, BANGSAR SOUTH CITY, KUALA LUMPUR
  EAST BRIGHT FZ-LLC
Notify: EAST BRIGHT FZ-LLC
Port of Loading (POL): NANTONG, CHINA (CNNTG)
POD: KARACHI, PAKISTAN (PKKHI)
Total Containers: 6 x 40'HC
Gross Wt (kgs): 131,058 KG
"""


@pytest.fixture()
def si_path(tmp_path):
    path = tmp_path / "email_999_SI.txt"
    path.write_text(DOCUMENT, encoding="utf-8")
    return str(path)


def _fake_model(monkeypatch, answer: dict) -> list[str]:
    """Point the model tier at a canned reply. Returns a call log."""
    calls: list[str] = []

    def fake_complete_json(prompt, *, schema_hint, max_tokens=512):
        calls.append(prompt)
        return answer

    monkeypatch.setattr(model_adapter, "available", lambda: True)
    monkeypatch.setattr(model_adapter, "complete_json", fake_complete_json)
    return calls


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------
def test_value_present_in_the_document_is_accepted(monkeypatch, si_path) -> None:
    _fake_model(monkeypatch, {"consignee": "EAST BRIGHT FZ-LLC"})

    doc = extract(si_path)

    assert doc.fields["consignee"].value == "EAST BRIGHT FZ-LLC"
    assert doc.fields["consignee"].decided_by == "model"
    # The rule tier's own fields are untouched.
    assert doc.fields["shipper"].decided_by == "rule"


def test_invented_value_is_discarded(monkeypatch, si_path) -> None:
    """The failure this gate exists for: a plausible company that is not there."""
    _fake_model(monkeypatch, {"consignee": "PACIFIC STAR TRADING SDN BHD"})

    doc = extract(si_path)

    assert "consignee" not in doc.fields, (
        "a value absent from the document must be dropped, leaving the field "
        "missing so the email escalates"
    )


def test_tidied_spacing_and_case_still_pass(monkeypatch, si_path) -> None:
    """Models reformat. The gate casefolds and collapses whitespace."""
    _fake_model(monkeypatch, {"consignee": "east bright   fz-llc"})

    doc = extract(si_path)

    assert doc.fields["consignee"].value == "east bright   fz-llc"


def test_partially_invented_value_is_discarded(monkeypatch, si_path) -> None:
    """Half right is still wrong: the whole string must be on the page."""
    _fake_model(monkeypatch, {"consignee": "EAST BRIGHT FZ-LLC, DUBAI"})

    doc = extract(si_path)

    assert "consignee" not in doc.fields


# --------------------------------------------------------------------------
# The model must never be a crutch
# --------------------------------------------------------------------------
def test_model_is_not_called_when_the_rules_found_everything(monkeypatch, tmp_path) -> None:
    complete = DOCUMENT.replace("Notify:", "Consignee: EAST BRIGHT FZ-LLC\nNotify:")
    path = tmp_path / "email_998_SI.txt"
    path.write_text(complete, encoding="utf-8")
    calls = _fake_model(monkeypatch, {"consignee": "SHOULD NOT BE ASKED"})

    doc = extract(str(path))

    assert calls == [], "no missing field means no model call"
    assert doc.fields["consignee"].decided_by == "rule"


def test_use_model_false_never_calls_the_model(monkeypatch, si_path) -> None:
    calls = _fake_model(monkeypatch, {"consignee": "EAST BRIGHT FZ-LLC"})

    doc = extract(si_path, use_model=False)

    assert calls == []
    assert "consignee" not in doc.fields


def test_provider_failure_leaves_the_rule_tier_result(monkeypatch, si_path) -> None:
    """A provider outage escalates the email. It never guesses."""

    def boom(prompt, *, schema_hint, max_tokens=512):
        raise model_adapter.ModelUnavailable("provider down")

    monkeypatch.setattr(model_adapter, "available", lambda: True)
    monkeypatch.setattr(model_adapter, "complete_json", boom)

    doc = extract(si_path)

    assert "consignee" not in doc.fields
    assert doc.fields["shipper"].value == "APRIL FAR EAST (M) SDN BHD"


def test_no_key_configured_is_not_an_error(monkeypatch, si_path) -> None:
    monkeypatch.setattr(model_adapter, "available", lambda: False)

    doc = extract(si_path)

    assert doc.readable is True
    assert "consignee" not in doc.fields


# --------------------------------------------------------------------------
# The gate function on its own
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [
        ("EAST BRIGHT FZ-LLC", True),
        ("east bright fz-llc", True),
        ("  EAST   BRIGHT  FZ-LLC ", True),
        ("EAST BRIGHT FZ LLC", False),   # punctuation changed
        ("EAST BRIGHT", True),           # a substring really is on the page
        ("NOT ON THIS PAGE LTD", False),
        ("", False),
    ],
)
def test_verify_against_source(value: str, expected: bool) -> None:
    assert verify_against_source(value, DOCUMENT) is expected
