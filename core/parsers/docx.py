"""Word SI and BL parser.  OWNER: Sheng Kuan.

8 attachments, all table-based, all with BILINGUAL labels of the form
"Port of Loading (<chinese>)" and "Gross Weight (<chinese> KGS)".
aliases.normalise_label already strips the CJK - use it, do not re-implement.
"""
from __future__ import annotations

from core.parsers import register
from core.types import ExtractedDoc


class DocxParser:
    extensions = (".docx",)

    def parse(self, path: str) -> ExtractedDoc:
        # TODO(sheng-kuan): python-docx. Walk doc.tables; cell 0 is the label,
        # cell 1 the value. Also walk paragraphs for the title line.
        raise NotImplementedError("Sheng Kuan owns core/parsers/docx.py")


register(DocxParser())
