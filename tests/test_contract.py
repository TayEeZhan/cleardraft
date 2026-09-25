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


def test_candidate_docs_returns_every_si_and_bl_in_attachment_order() -> None:
    docs = [
        ExtractedDoc(path="first_BL.txt", kind="BL"),
        ExtractedDoc(path="only_SI.txt", kind="SI"),
        ExtractedDoc(path="second_BL.txt", kind="BL"),
        ExtractedDoc(path="notes.txt", kind="OTHER"),
    ]
    sis, bls = pipeline.candidate_docs(docs)
    assert [doc.path for doc in sis] == ["only_SI.txt"]
    assert [doc.path for doc in bls] == ["first_BL.txt", "second_BL.txt"]


def test_split_si_bl_preserves_documented_first_candidate_default() -> None:
    docs = [
        ExtractedDoc(path="first_BL.txt", kind="BL"),
        ExtractedDoc(path="only_SI.txt", kind="SI"),
        ExtractedDoc(path="second_BL.txt", kind="BL"),
    ]
    si, bl = pipeline.split_si_bl(docs)
    assert si.path == "only_SI.txt"
    assert bl.path == "first_BL.txt"


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


def test_weight_units_cannot_be_silently_discarded() -> None:
    assert norm.normalise_weight("22 MT") == 22000
    assert norm.normalise_weight("22 KG") == 22
    assert norm.normalise_weight("22 MT") != norm.normalise_weight("22 KG")
    assert norm.normalise_weight("22 STONE") is None


def test_supported_weight_units_are_compared_in_kilograms() -> None:
    from core.types import FieldValue

    tonnes = FieldValue(value="22 MT", raw="Gross Weight: 22 MT", line_no=1, label="Gross Weight")
    kilograms = FieldValue(value="22 KG", raw="Gross Weight: 22 KG", line_no=1, label="Gross Weight")
    rows = compare_mod.compare(
        ExtractedDoc(path="x_SI.txt", kind="SI", fields={"gross_weight_kg": tonnes}),
        ExtractedDoc(path="x_BL.txt", kind="BL", fields={"gross_weight_kg": kilograms}),
    )
    weight = next(row for row in rows if row.field == "gross_weight_kg")
    assert weight.undecidable is False
    assert weight.matched is False
    assert (weight.si_norm, weight.bl_norm) == (22000, 22)


def test_official_lowercase_tonne_symbol_matches_kilograms() -> None:
    from core.types import FieldValue

    tonnes = FieldValue(value="18.20 t", raw="Gross Weight: 18.20 t", line_no=1, label="Gross Weight")
    kilograms = FieldValue(value="18,200 KG", raw="Gross Weight: 18,200 KG", line_no=1, label="Gross Weight")
    rows = compare_mod.compare(
        ExtractedDoc(path="x_SI.pdf", kind="SI", fields={"gross_weight_kg": tonnes}),
        ExtractedDoc(path="x_BL.pdf", kind="BL", fields={"gross_weight_kg": kilograms}),
    )
    weight = next(row for row in rows if row.field == "gross_weight_kg")
    assert weight.undecidable is False
    assert weight.matched is True
    assert (weight.si_norm, weight.bl_norm) == (18200, 18200)


def test_capitalised_tonne_symbol_matches_after_document_extraction() -> None:
    """All-caps forms/OCR may render the official ``t`` symbol as ``T``."""
    from core.types import FieldValue

    tonnes = FieldValue(value="18.20 T", raw="GROSS WEIGHT: 18.20 T", line_no=1, label="GROSS WEIGHT")
    kilograms = FieldValue(value="18,200 KG", raw="Gross Weight: 18,200 KG", line_no=1, label="Gross Weight")
    rows = compare_mod.compare(
        ExtractedDoc(path="x_SI.pdf", kind="SI", fields={"gross_weight_kg": tonnes}),
        ExtractedDoc(path="x_BL.pdf", kind="BL", fields={"gross_weight_kg": kilograms}),
    )
    weight = next(row for row in rows if row.field == "gross_weight_kg")
    assert weight.undecidable is False
    assert weight.matched is True
    assert (weight.si_norm, weight.bl_norm) == (18200, 18200)


def test_unsupported_weight_unit_escalates_the_comparison() -> None:
    from core.types import FieldValue

    unsupported = FieldValue(value="22 STONE", raw="Gross Weight: 22 STONE", line_no=1, label="Gross Weight")
    kilograms = FieldValue(value="22 KG", raw="Gross Weight: 22 KG", line_no=1, label="Gross Weight")
    rows = compare_mod.compare(
        ExtractedDoc(path="x_SI.txt", kind="SI", fields={"gross_weight_kg": unsupported}),
        ExtractedDoc(path="x_BL.txt", kind="BL", fields={"gross_weight_kg": kilograms}),
    )
    weight = next(row for row in rows if row.field == "gross_weight_kg")
    assert weight.undecidable is True
    assert weight.matched is False


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


# ---------------------------------------------------------------------------
# The single model door (ADR-004). Every model call in the system passes
# through adapters/model.py, so "N% of decisions were made by a rule" is only
# defensible if this is the one entry point and it fails loudly.
# ---------------------------------------------------------------------------
def test_model_client_is_not_a_stub() -> None:
    """Regression: complete_json raised NotImplementedError, so the entire
    model fallback was dead. Rules covered this dataset, so nothing failed -
    it would only have surfaced on unseen data, which is the final round."""
    import inspect

    from adapters import model as model_mod

    src = inspect.getsource(model_mod.complete_json)
    assert "NotImplementedError" not in src


def test_model_absence_raises_model_unavailable_not_a_guess() -> None:
    """With no key the pipeline must escalate, never invent an answer."""
    import os

    from adapters.model import ModelUnavailable, complete_json

    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        raised = False
        try:
            complete_json("anything", schema_hint="{}")
        except ModelUnavailable:
            raised = True
        assert raised, "missing key must raise ModelUnavailable"
    finally:
        if saved is not None:
            os.environ["ANTHROPIC_API_KEY"] = saved


def test_model_reply_parsing_tolerates_fences_and_rejects_prose() -> None:
    from adapters.model import _extract_json

    assert _extract_json(chr(123) + chr(34) + "category" + chr(34) + ":" + chr(34) + "SPAM" + chr(34) + chr(125)) == {"category": "SPAM"}
    fenced = "```json" + chr(10) + chr(123) + chr(34) + "category" + chr(34) + ":" + chr(34) + "GENERAL" + chr(34) + chr(125) + chr(10) + "```"
    assert _extract_json(fenced) == {"category": "GENERAL"}
    for junk in ("I cannot help with that.", "[1,2,3]", ""):
        rejected = False
        try:
            _extract_json(junk)
        except Exception:
            rejected = True
        assert rejected, junk


# ---------------------------------------------------------------------------
# PDF glyph interleaving. A long label that overflows into the value column
# comes back with the two physically overlapping; no text extractor can
# separate them. The danger is that the garbage still parses as a company
# name, so a correct Bill of Lading gets reported as wrong.
# ---------------------------------------------------------------------------
def test_interleaved_pdf_label_is_detected_not_compared() -> None:
    from core.parsers.pdf import _looks_interleaved

    # real lines from the corpus: label "Notify Party/Intermediate Consignee"
    # overlapping the values "KTP CO., LTD", "CERIEX" and "NAGAPPA EXPORTS"
    for corrupt in (
        "Party/Intermediate ConsKiTgPne CeO., LTD",
        "Party/Intermediate ConsCigEnReIEeX",
        "Party/Intermediate ConsNigAnGeAePPA EXPORTS",
    ):
        assert _looks_interleaved("notify", corrupt), corrupt

    # ordinary values must never be mistaken for interleaving
    for clean in (
        "KTP CO., LTD",
        "UAB NOVAKOPA",
        "APRIL FINE PAPER TRADING (MIDDLE EAST) FZE",
        "PARTNERS IN PAPER LLC",
        "",
    ):
        assert not _looks_interleaved("notify", clean), clean


# ---------------------------------------------------------------------------
# Re-check of an amended draft. The demo amendment is hand-written to hit all
# three outcomes, so this pins the behaviour the demo video depends on.
# ---------------------------------------------------------------------------
def test_recheck_sorts_fixed_still_wrong_and_newly_broken() -> None:
    from core.extract import extract
    from core.recheck import all_clear, recheck

    si = extract(os.path.join(DATA, "attachments", "email_004_SI.txt"))
    v1 = extract(os.path.join(DATA, "attachments", "email_004_BL.txt"))
    v2 = extract(os.path.join(DATA, "demo", "email_004_BL_v2.txt"))
    outcome = {r.field: r.outcome for r in recheck(si, v1, v2)}

    assert outcome["consignee"] == "fixed"
    assert outcome["notify_party"] == "still_wrong"
    # the one a tired clerk misses: correcting one field broke another
    assert outcome["container_count"] == "newly_broken"
    assert outcome["shipper"] == "ok"
    assert not all_clear(recheck(si, v1, v2))
    # re-checking the ORIGINAL draft against itself changes nothing
    assert all(r.outcome in ("ok", "still_wrong") for r in recheck(si, v1, v1))


def test_recheck_reply_names_the_newly_broken_field() -> None:
    from core.extract import extract
    from core.recheck import recheck
    from core.reply import draft_recheck_reply

    si = extract(os.path.join(DATA, "attachments", "email_004_SI.txt"))
    v1 = extract(os.path.join(DATA, "attachments", "email_004_BL.txt"))
    v2 = extract(os.path.join(DATA, "demo", "email_004_BL_v2.txt"))
    email = Email(email_id="email_004", sender="a@b.com",
                  subject="REQUEST BL DRAFT", body="Hi Mitchelle, OC 5ALT-01226.")
    text = draft_recheck_reply(email, recheck(si, v1, v2))
    assert "Container Count" in text and "now wrong" in text
    assert "Notify Party" in text
    assert "OK to proceed" not in text


# ---------------------------------------------------------------------------
# From the silent-failure review of the model path.
# ---------------------------------------------------------------------------
def test_unclassifiable_email_goes_to_a_human_not_ok() -> None:
    """No rule matched and the model could not answer. Returning OK here was a
    confident wrong answer - the one thing this system promises never to give."""
    from core.classify import classify
    from core.decide import decide

    email = Email(email_id="x", sender="a@b.com", subject="Hello",
                  body="Can you take a look?")
    c = classify(email, use_model=True)          # model is off in tests
    d = decide(email, c, None, None, ())
    assert d.status == "NEEDS_REVIEW"
    assert d.review_reason == "unclassified"
    assert d.has_defect is False


def test_classifier_evidence_gate_matches_the_extraction_gate() -> None:
    """A genuine quote the model re-wrapped across lines must pass; a quote
    that is not in the email must not. Same rule as field extraction."""
    from core.extract import verify_against_source

    body = "Please verify the draft" + chr(10) + "bill of lading against our SI."
    assert verify_against_source("verify the draft bill of lading", body)
    assert not verify_against_source("please cancel the invoice", body)
