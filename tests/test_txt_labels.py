"""Plain-text label handling: column-aligned labels and empty label rows.

OWNER: Sheng Kuan.

.txt is 192 of the 250 attachments and the format a real export arrives in.
The organiser's generator always writes "Label: value" on one line, so none
of the shapes below occur in the provided corpus - these tests pin behaviour
we will only meet in the final round, on documents nobody has seen.

The distinction that matters here: an EMPTY value means the text sits on the
next line, while a BLANK TOKEN ("???", "TBA") means the sender deliberately
left the field empty and the email must escalate as missing_value. Filling a
blank token from the line below would turn a correct escalation into a
confident wrong answer.
"""
from __future__ import annotations

import pytest

from core.parsers.txt import TxtParser


def _parse(tmp_path, body: str, name: str = "email_900_SI.txt"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return TxtParser().parse(str(path))


# --------------------------------------------------------------------------
# Finding 3: labels aligned into columns, with no colon
# --------------------------------------------------------------------------
def test_column_aligned_label_is_read(tmp_path) -> None:
    doc = _parse(
        tmp_path,
        "SHIPPING INSTRUCTION\n"
        "Shipper      APRIL FAR EAST (M) SDN BHD\n"
        "Load Port    NANTONG, CHINA (CNNTG)\n",
    )
    assert doc.fields["shipper"].value == "APRIL FAR EAST (M) SDN BHD"
    assert doc.fields["port_of_loading"].value == "NANTONG, CHINA (CNNTG)"


def test_colon_form_still_wins(tmp_path) -> None:
    doc = _parse(
        tmp_path,
        "SHIPPING INSTRUCTION\nShipper: APRIL FAR EAST (M) SDN BHD\n",
    )
    assert doc.fields["shipper"].value == "APRIL FAR EAST (M) SDN BHD"
    assert doc.fields["shipper"].label == "Shipper"


def test_unknown_line_without_a_colon_is_ignored(tmp_path) -> None:
    doc = _parse(
        tmp_path,
        "SHIPPING INSTRUCTION\nRAKEZ AMENITY CENTER AL HAMRA INDUSTRIAL ZONE\n",
    )
    assert doc.fields == {}


# --------------------------------------------------------------------------
# Finding 4: the label row is empty and the name sits on the next line
# --------------------------------------------------------------------------
def test_empty_row_takes_the_indented_line(tmp_path) -> None:
    doc = _parse(
        tmp_path,
        "SHIPPING INSTRUCTION\n"
        "Shipper:\n"
        "  APRIL FAR EAST (M) SDN BHD\n"
        "  TOWER 2, AVENUE 5, KUALA LUMPUR\n",
    )
    assert doc.fields["shipper"].value == "APRIL FAR EAST (M) SDN BHD"


def test_only_the_first_continuation_line_is_taken(tmp_path) -> None:
    """The address must never join the value - that is how false mismatches
    are manufactured when the SI and BL wrap differently."""
    doc = _parse(
        tmp_path,
        "SHIPPING INSTRUCTION\n"
        "Consignee:\n"
        "  EAST BRIGHT FZ-LLC\n"
        "  RAKEZ AMENITY CENTER, RAK, UAE\n",
    )
    assert doc.fields["consignee"].value == "EAST BRIGHT FZ-LLC"


def test_evidence_points_at_the_line_the_value_came_from(tmp_path) -> None:
    doc = _parse(
        tmp_path,
        "SHIPPING INSTRUCTION\nShipper:\n  APRIL FAR EAST (M) SDN BHD\n",
    )
    field = doc.fields["shipper"]
    assert field.line_no == 3
    assert field.raw.strip() == "APRIL FAR EAST (M) SDN BHD"


def test_blank_token_is_never_filled_from_the_next_line(tmp_path) -> None:
    """The escalation this whole rule is careful about."""
    doc = _parse(
        tmp_path,
        "SHIPPING INSTRUCTION\n"
        "Consignee: ???\n"
        "  EAST BRIGHT FZ-LLC\n",
    )
    assert "consignee" in doc.fields
    assert doc.fields["consignee"].value == "", (
        "'???' means the sender left it blank; taking the line below turns a "
        "missing_value escalation into a confident wrong answer"
    )


def test_a_filled_value_ignores_its_address_block(tmp_path) -> None:
    doc = _parse(
        tmp_path,
        "SHIPPING INSTRUCTION\n"
        "Shipper: APRIL FAR EAST (M) SDN BHD\n"
        "  TOWER 2, AVENUE 5, KUALA LUMPUR\n",
    )
    assert doc.fields["shipper"].value == "APRIL FAR EAST (M) SDN BHD"


def test_empty_row_at_end_of_file_is_safe(tmp_path) -> None:
    doc = _parse(tmp_path, "SHIPPING INSTRUCTION\nShipper:\n")
    assert doc.fields["shipper"].value == ""


def test_empty_row_followed_by_another_label_stays_blank(tmp_path) -> None:
    """Only an INDENTED line continues a row."""
    doc = _parse(
        tmp_path,
        "SHIPPING INSTRUCTION\nShipper:\nConsignee: EAST BRIGHT FZ-LLC\n",
    )
    assert doc.fields["shipper"].value == ""
    assert doc.fields["consignee"].value == "EAST BRIGHT FZ-LLC"


# --------------------------------------------------------------------------
# The text path must stay independent of the PDF library
# --------------------------------------------------------------------------
def test_txt_parses_with_pdfplumber_missing(tmp_path, monkeypatch) -> None:
    """txt.py must not reach pdfplumber through a shared helper.

    Importing the matcher from pdf.py instead of core.parsers.labels would
    undo the registry guard: one missing library would again take down all
    192 .txt attachments.
    """
    import importlib
    import sys

    monkeypatch.setitem(sys.modules, "pdfplumber", None)
    for name in [m for m in list(sys.modules) if m.startswith("core.parsers")]:
        del sys.modules[name]
    try:
        txt_module = importlib.import_module("core.parsers.txt")
        path = tmp_path / "email_901_SI.txt"
        path.write_text(
            "SHIPPING INSTRUCTION\nShipper: APRIL FAR EAST (M) SDN BHD\n",
            encoding="utf-8",
        )
        doc = txt_module.TxtParser().parse(str(path))
        assert doc.readable is True
        assert doc.fields["shipper"].value == "APRIL FAR EAST (M) SDN BHD"
    finally:
        monkeypatch.undo()
        for name in [m for m in list(sys.modules) if m.startswith("core.parsers")]:
            del sys.modules[name]
        importlib.import_module("core.parsers")


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Shipper      ABC PAPER LTD", "ABC PAPER LTD"),
        ("Shipper: ABC PAPER LTD", "ABC PAPER LTD"),
        ("Shipper:ABC PAPER LTD", "ABC PAPER LTD"),
    ],
)
def test_separator_variants(tmp_path, line: str, expected: str) -> None:
    doc = _parse(tmp_path, f"SHIPPING INSTRUCTION\n{line}\n")
    assert doc.fields["shipper"].value == expected
