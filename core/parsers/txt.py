"""Plain-text SI and BL parser.  OWNER: Sheng Kuan.

192 of the 250 attachments are .txt, so this adapter carries most of the load.
Format: one "Label: value" per line, with entity addresses continuing on the
following indented line.
"""
from __future__ import annotations

from core import aliases
from core.parsers import detect_kind, register
from core.types import CompareField, DocKind, ExtractedDoc, FieldValue

#: Keyword test for the document heading. Shared with xlsx.py (imported from
#: there) so both adapters classify a heading the same way.
_SI_MARKERS = ("SHIPPING INSTRUCTION",)
_BL_MARKERS = ("BILL OF LADING",)
#: These map to OTHER explicitly (as opposed to falling through by default)
#: so the intent is documented even though the outcome is the same.
_OTHER_MARKERS = ("COMMERCIAL INVOICE", "PACKING LIST", "CERTIFICATE OF ORIGIN")


def classify_kind_from_text(line: str) -> DocKind:
    """Classify one heading line.

    Delegates to the shared rule in core.parsers so all four format adapters
    agree. They must: the SI is titled "SHIPPING INSTRUCTION" in .txt but
    "BL INSTRUCTION" in .xlsx and "BILL OF LADING INSTRUCTION" in .pdf, and a
    per-parser keyword list drifts apart the moment one of them is edited.
    """
    return detect_kind(line)


class TxtParser:
    extensions = (".txt",)

    def parse(self, path: str) -> ExtractedDoc:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            return ExtractedDoc(path=path, kind="UNREADABLE", readable=False, error=str(exc))

        try:
            if not text.strip():
                return ExtractedDoc(
                    path=path, kind="UNREADABLE", readable=False, error="empty file",
                )

            lines = text.splitlines()

            # kind: first non-blank line, by keyword.
            kind: DocKind = "OTHER"
            for line in lines:
                if line.strip():
                    kind = classify_kind_from_text(line)
                    break

            fields: dict[CompareField, FieldValue] = {}
            for i, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                if line[:1].isspace():
                    # Indented continuation line: an address wrapped onto the
                    # next line. Both SI and BL carry these, but appending
                    # them is only safe if every renderer wraps identically -
                    # the moment one differs it manufactures a false
                    # shipper/consignee mismatch. Take the label line only.
                    continue
                if ":" not in line:
                    continue

                label, _, value = line.partition(":")
                label = label.strip()
                value = value.strip()

                field = aliases.field_for_label(label)
                if field is None or field in fields:
                    # Unknown label, or the field is already captured - keep
                    # the FIRST occurrence, not the last.
                    continue

                # A blank token ("???", "TBA", ...) means the row exists but
                # was left empty. Store it as "", never omit the field: a
                # missing key means "no such row", which is a different
                # escalation reason from "present but blank".
                stored_value = "" if aliases.is_blank(value) else value

                fields[field] = FieldValue(
                    value=stored_value,
                    raw=line,
                    line_no=i,
                    label=label,
                    decided_by="rule",
                )

            return ExtractedDoc(path=path, kind=kind, fields=fields, text=text, readable=True)
        except Exception as exc:  # pragma: no cover - defensive; parse() must never raise
            return ExtractedDoc(path=path, kind="UNREADABLE", readable=False, error=str(exc))


register(TxtParser())
