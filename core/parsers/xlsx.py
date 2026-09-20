"""Spreadsheet SI and BL parser.  OWNER: Sheng Kuan.

22 attachments. Two columns: label in A, value in B.
WATCH OUT: gross weight is stored as a NUMBER, not a formatted string, so
there are no thousands separators and no "KG" suffix. line_no is the row.
"""
from __future__ import annotations

from core.parsers import register
from core.types import ExtractedDoc


class XlsxParser:
    extensions = (".xlsx",)

    def parse(self, path: str) -> ExtractedDoc:
        # TODO(sheng-kuan): openpyxl, read_only=True, data_only=True.
        # Build `text` as "label: value" lines so the verification gate works
        # the same way it does for txt.
        raise NotImplementedError("Sheng Kuan owns core/parsers/xlsx.py")


register(XlsxParser())
