"""PDF SI and BL parser.  OWNER: Sheng Kuan.

28 attachments. Three traps, all deliberate:
  1. some PDFs are IMAGE-ONLY with no text layer -> readable=False,
     error="no text layer". This is the `unreadable` escalation reason.
  2. some are truncated/corrupt (valid header, random bytes, no EOF) ->
     readable=False, error="corrupt".
  3. ports are written WITHOUT the UN/LOCODE here, while the .txt renderer
     writes them WITH it. Do not try to fix that here - core/normalise.py
     strips the code on both sides. Just return what is on the page.
"""
from __future__ import annotations

from core.parsers import register
from core.types import ExtractedDoc


class PdfParser:
    extensions = (".pdf",)

    def parse(self, path: str) -> ExtractedDoc:
        # TODO(sheng-kuan): pdfplumber or pypdf. Wrap the whole read in
        # try/except and return an UNREADABLE doc on failure. Never raise.
        # If extracted text is shorter than ~40 chars, treat as image-only.
        raise NotImplementedError("Sheng Kuan owns core/parsers/pdf.py")


register(PdfParser())
