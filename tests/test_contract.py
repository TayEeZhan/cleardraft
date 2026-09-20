"""Contract tests. These must pass before anybody merges to main.

They prove the plumbing, not the accuracy. Accuracy lives in eval/.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.inbox import LocalInbox
from core import aliases, compare as compare_mod, normalise as norm, pipeline
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


# ---------------------------------------------------------------------------
# Normaliser regression guards.
#
# These live here rather than in a unit-test file because they guard the
# failure mode that costs the most and shows the least: a normaliser that is
# too aggressive silently collapses two DIFFERENT values into one, deleting a
# real defect with no error anywhere. End-to-end is 50% of the score.
#
# Zi Qi: your broader normaliser unit tests are still yours. These seven are
# the ones that must never be deleted.
# ---------------------------------------------------------------------------
def test_weight_survives_a_decimal_point() -> None:
    """Regression: stripping every non-digit turned "21,577.00" into 2157700.

    A 100x error that guarantees a false mismatch, and "21,577.00 KGS" is a
    very common real-world rendering.
    """
    assert norm.normalise_weight("21,577.00 KGS") == 21577
    assert norm.normalise_weight("21577.0") == 21577
    assert norm.normalise_weight(21577.0) == 21577
    assert norm.normalise_weight("21.577") == 21577      # European thousands
    assert norm.normalise_weight("21,577 KG") == 21577
    assert norm.normalise_weight("341715") == 341715     # bare xlsx integer


def test_normalisers_absorb_formatting() -> None:
    assert norm.normalise_port("CALLAO, PERU (PECLL)") == norm.normalise_port("CALLAO, PERU")
    assert norm.normalise_container_count("6 x 40'HC") == 6
    assert norm.normalise_container_count("1 x 20GP") == 1
    assert norm.normalise_entity("MOORIM SP CO., LTD") == norm.normalise_entity("MOORIM SP CO LTD")


def test_normalisers_do_not_collapse_real_defects() -> None:
    """The silent killer. Every planted defect is substantive, so any
    normaliser that merges these has destroyed the end-to-end metric."""
    assert norm.normalise_entity("MOORIM SP CO., LTD") != norm.normalise_entity("UAB NOVAKOPA")
    assert norm.normalise_port("CALLAO, PERU") != norm.normalise_port("AQABA, JORDAN")
    assert norm.normalise_container_count("3 x 40'HC") != norm.normalise_container_count("4 x 40'HC")
    assert norm.normalise_weight("21,577 KG") != norm.normalise_weight("22,077 KG")
    assert norm.normalise_weight("21,577 KG") != norm.normalise_weight("21,077 KG")


def test_blank_tokens_normalise_to_none_not_empty_string() -> None:
    """Two blank fields must be UNDECIDABLE, never a match against each other."""
    for token in ("", "???", "_______", "TBA", "N/A"):
        assert norm.normalise("shipper", token) is None, token
        assert norm.normalise("gross_weight_kg", token) is None, token


def test_compare_always_returns_seven_ordered_rows() -> None:
    rows = compare_mod.compare(
        ExtractedDoc(path="a_SI.txt", kind="SI"),
        ExtractedDoc(path="a_BL.txt", kind="BL"),
    )
    assert len(rows) == 7
    assert tuple(r.field for r in rows) == COMPARE_FIELDS


def test_compare_never_raises_on_garbage() -> None:
    """One malformed document must not end a 520-email batch."""
    for si, bl in ((None, None), (ExtractedDoc(path="x", kind="SI"), None)):
        rows = compare_mod.compare(si, bl)
        assert len(rows) == 7
        assert all(r.undecidable and not r.matched for r in rows)


def test_compare_marks_a_blank_side_undecidable_not_matched() -> None:
    from core.types import FieldValue

    blank = FieldValue(value="", raw="Shipper: ???", line_no=3, label="Shipper")
    real = FieldValue(value="APRIL FAR EAST (M) SDN BHD", raw="", line_no=3, label="Shipper")
    rows = compare_mod.compare(
        ExtractedDoc(path="s", kind="SI", fields={"shipper": blank}),
        ExtractedDoc(path="b", kind="BL", fields={"shipper": real}),
    )
    shipper = next(r for r in rows if r.field == "shipper")
    assert shipper.undecidable is True
    assert shipper.matched is False


# ---------------------------------------------------------------------------
# Guards for the three defects the silent-failure review surfaced.
# ---------------------------------------------------------------------------
def test_port_strips_only_locode_shaped_brackets() -> None:
    """Regression: stripping ANY trailing bracket collapsed genuinely
    different places. "NEW YORK (APM TERMINAL)" and "NEW YORK (RED HOOK
    TERMINAL)" are not the same port, and merging them deletes a real defect
    with no error anywhere."""
    # a UN/LOCODE is noise and must go (.txt carries it, .pdf does not)
    assert norm.normalise_port("CALLAO, PERU (PECLL)") == norm.normalise_port("CALLAO, PERU")
    # a non-trailing bracket is part of the port name and must survive
    assert norm.normalise_port("PORT KLANG (WESTPORT), MALAYSIA (MYPKG)") == norm.normalise_port("PORT KLANG (WESTPORT), MALAYSIA")
    assert "WESTPORT" in norm.normalise_port("PORT KLANG (WESTPORT), MALAYSIA (MYPKG)")
    # anything not LOCODE-shaped is content, not noise
    assert norm.normalise_port("NEW YORK (APM TERMINAL)") != norm.normalise_port("NEW YORK (RED HOOK TERMINAL)")


def test_reply_never_claims_a_check_that_did_not_run() -> None:
    """A clean-check reply on an email where no documents were compared tells
    the clerk a verification happened when none did."""
    from core import reply as reply_mod
    from core.types import Decision

    email = Email(
        email_id="email_002",
        sender="a@b.com",
        subject="RE_ LOCAL CHARGES FOB - 5AKR-61849",
        body="Hi Najiha, query on the invoice.",
    )
    invoice = Decision(
        email_id="email_002", category="INVOICE_QUERY", status="OK",
        review_reason=None, has_defect=False, defect_fields=(),
        decided_by="rule", comparisons=(),
    )
    text = reply_mod.draft_reply(email, invoice)
    assert "OK to proceed" not in text
    assert "No mismatch detected" not in text


def test_reply_recovers_the_reference_from_the_body() -> None:
    """55 of 520 emails carry the OC reference only in the body. Drafting
    "draft BL for email_004" is useless to a clerk."""
    from core.reply import _reference

    email = Email(
        email_id="email_004",
        sender="a@b.com",
        subject="REQUEST BL DRAFT _ PO 26067_ COATED IVORY BOARD__138MT",
        body="Hi Mitchelle, attached are the SI and draft BL for OC 5ALT-01226.",
    )
    assert _reference(email) == "5ALT-01226"
