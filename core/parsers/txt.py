"""Plain-text SI and BL parser.  OWNER: Sheng Kuan.

192 of the 250 attachments are .txt, so this adapter carries most of the load.
Format: one "Label: value" per line, with entity addresses continuing on the
following indented line.
"""
from __future__ import annotations

from core.parsers import register
from core.types import ExtractedDoc


class TxtParser:
    extensions = (".txt",)

    def parse(self, path: str) -> ExtractedDoc:
        # TODO(sheng-kuan): implement.
        #  1. read utf-8 with errors="replace"; empty file -> UNREADABLE
        #  2. detect kind from the first non-blank line:
        #       "SHIPPING INSTRUCTION"   -> SI
        #       "BILL OF LADING"         -> BL
        #       "COMMERCIAL INVOICE" / "PACKING LIST" / "CERTIFICATE OF ORIGIN"
        #                                -> OTHER   (drives wrong_doc_type)
        #  3. for each line, split on the FIRST ":" -> (label, value)
        #     resolve label via aliases.field_for_label
        #     record FieldValue(value, raw=line, line_no=i+1, label=label)
        #  4. keep the FIRST occurrence of a field, not the last
        #  5. return text as the joined lines so the verification gate can search
        raise NotImplementedError("Sheng Kuan owns core/parsers/txt.py")


register(TxtParser())
