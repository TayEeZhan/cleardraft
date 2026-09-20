"""Contract tests. These must pass before anybody merges to main.

They prove the plumbing, not the accuracy. Accuracy lives in eval/.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.inbox import LocalInbox
from core import aliases, pipeline
from core.types import COMPARE_FIELDS, Decision, Email, ExtractedDoc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def test_every_email_parses() -> None:
    inbox = LocalInbox(DATA)
    emails = list(inbox.emails())
    assert len(emails) == 520
    assert all(isinstance(e, Email) for e in emails)
    assert all(e.email_id for e in emails)


def test_submission_shape_matches_organiser_sample() -> None:
    """Our record keys must be a superset of the sample's.

    `decided_by` is an extra the scorer reads for its rule_pct diagnostic.
    Anything missing here scores zero regardless of how good the logic is.
    """
    with open(os.path.join(DATA, "sample_submission.json"), encoding="utf-8") as fh:
        sample = json.load(fh)
    required = set(next(iter(sample.values())))

    d = Decision(
        email_id="email_001", category="BL_COMPARISON", status="MISMATCH",
        review_reason=None, has_defect=True,
        defect_fields=("container_count", "consignee"), decided_by="rule",
    )
    record = d.to_submission()
    assert required <= set(record)
    # sorted, because the scorer compares sets and we want stable diffs
    assert record["defect_fields"] == ["consignee", "container_count"]


def test_alias_table_is_unambiguous() -> None:
    """No label may map to two different fields."""
    seen: dict[str, str] = {}
    for fld, names in aliases.ALIASES.items():
        for name in names:
            key = aliases.normalise_label(name)
            assert seen.get(key, fld) == fld, f"{name!r} is ambiguous"
            seen[key] = fld


def test_cjk_labels_normalise_to_their_english_alias() -> None:
    """The .docx renderer emits bilingual labels. They must still resolve."""
    assert aliases.field_for_label("Port of Loading (\u88c5\u8d27\u6e2f)") == "port_of_loading"
    assert aliases.field_for_label("Gross Weight (\u6bdb\u91cd KGS)") == "gross_weight_kg"
    assert aliases.field_for_label("Load Port") == "port_of_loading"
    assert aliases.field_for_label("To the Order of") == "consignee"


def test_blank_tokens_are_recognised() -> None:
    for token in ("???", "_______", "TBA", "tbc", "N/A", ""):
        assert aliases.is_blank(token), token
    assert not aliases.is_blank("MOORIM SP CO., LTD")


def test_split_si_bl_falls_back_to_filename() -> None:
    docs = [
        ExtractedDoc(path="attachments/email_001_BL.txt", kind="OTHER"),
        ExtractedDoc(path="attachments/email_001_SI.txt", kind="OTHER"),
    ]
    si, bl = pipeline.split_si_bl(docs)
    assert si is not None and si.path.endswith("_SI.txt")
    assert bl is not None and bl.path.endswith("_BL.txt")


def test_seven_compare_fields_exactly() -> None:
    assert len(COMPARE_FIELDS) == 7
    assert len(set(COMPARE_FIELDS)) == 7
