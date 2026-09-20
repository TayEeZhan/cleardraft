"""Spreadsheet SI and BL parser.  OWNER: Sheng Kuan.

22 attachments. Two columns: label in A, value in B.
WATCH OUT: gross weight is stored as a NUMBER, not a formatted string, so
there are no thousands separators and no "KG" suffix. line_no is the row.
"""
from __future__ import annotations

import openpyxl

from core import aliases
from core.parsers import register
from core.parsers.txt import classify_kind_from_text
from core.types import CompareField, DocKind, ExtractedDoc, FieldValue

#: How many leading "label: value" lines to scan for a heading before giving
#: up and calling the document OTHER. The heading is never the first row in
#: this dataset (that is the company letterhead) but it is always near the
#: top, so an unbounded scan risks matching an unrelated cell deep in the
#: body that happens to contain one of the keyword phrases.
_HEADING_SCAN_LINES = 10


def _cell_to_str(value: object) -> str:
    """Render one cell's value as the plain string the rest of the pipeline
    expects.

    openpyxl hands back int/float for numeric cells (gross weight in
    particular). A float that is a whole number must not leak a trailing
    ".0" downstream - "21577.0" is not the same string as "21577" and would
    manufacture a false mismatch against a .txt sibling that reads "21577".
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


class XlsxParser:
    extensions = (".xlsx",)

    def parse(self, path: str) -> ExtractedDoc:
        try:
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:
            return ExtractedDoc(path=path, kind="UNREADABLE", readable=False, error=str(exc))

        try:
            if not wb.worksheets:
                return ExtractedDoc(
                    path=path, kind="UNREADABLE", readable=False, error="no worksheets",
                )
            ws = wb.worksheets[0]

            text_lines: list[str] = []
            fields: dict[CompareField, FieldValue] = {}

            for row in ws.iter_rows():
                if not row:
                    continue
                label_cell = row[0]
                value_cell = row[1] if len(row) > 1 else None

                label_raw = label_cell.value
                if label_raw is None or (isinstance(label_raw, str) and not label_raw.strip()):
                    continue

                label = _cell_to_str(label_raw)
                value_str = _cell_to_str(value_cell.value if value_cell is not None else None)
                text_lines.append(f"{label}: {value_str}")

                field = aliases.field_for_label(label)
                if field is None or field in fields:
                    # Unknown label, or already captured - keep the FIRST
                    # occurrence, not the last.
                    continue

                stored_value = "" if aliases.is_blank(value_str) else value_str
                fields[field] = FieldValue(
                    value=stored_value,
                    raw=f"{label}: {value_str}",
                    line_no=label_cell.row,
                    label=label,
                    decided_by="rule",
                )

            kind: DocKind = "OTHER"
            for line in text_lines[:_HEADING_SCAN_LINES]:
                candidate = classify_kind_from_text(line)
                if candidate in ("SI", "BL"):
                    kind = candidate
                    break

            text = "\n".join(text_lines)
            return ExtractedDoc(path=path, kind=kind, fields=fields, text=text, readable=True)
        except Exception as exc:  # pragma: no cover - defensive; parse() must never raise
            return ExtractedDoc(path=path, kind="UNREADABLE", readable=False, error=str(exc))
        finally:
            wb.close()


register(XlsxParser())
