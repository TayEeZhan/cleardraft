"""Label matching shared by the text-based parsers.  OWNER: Sheng Kuan.

NO THIRD-PARTY IMPORTS, DELIBERATELY.
`txt.py` uses this module, and `txt.py` must never reach code that imports
pdfplumber: a missing PDF library would then take down all 192 .txt
attachments, which is the exact failure the registry guard in __init__.py
exists to prevent. Keeping the shared logic here, rather than importing it
from pdf.py, is what keeps those two formats independent.
"""
from __future__ import annotations

from core import aliases
from core.types import CompareField


def match_label_prefix(line: str) -> "tuple[CompareField, str, str] | None":
    """The longest known alias `line` starts with, as (field, label, value).

    For documents that align labels into columns instead of using a colon:

        Shipper      APRIL FAR EAST (M) SDN BHD

    Two rules keep it honest:
      * the alias must be followed by a space or a colon, so "POL" cannot
        swallow a line that merely begins with those three letters;
      * aliases.ORDERED is longest-first, so "Port of Loading (POL)" is tried
        before the shorter "Port of Loading".
    """
    for alias in aliases.ORDERED:
        n = len(alias)
        if len(line) <= n:
            continue
        if line[:n].casefold() != alias:
            continue
        if line[n] not in (" ", ":"):
            continue
        field = aliases.field_for_label(alias)
        if field is None:
            continue
        return field, line[:n], line[n:].lstrip(" :").strip()
    return None
