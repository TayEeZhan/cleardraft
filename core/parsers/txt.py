"""Plain-text SI and BL parser.  OWNER: Sheng Kuan.

192 of the 250 attachments are .txt, so this adapter carries most of the load.
Format: one "Label: value" per line, with entity addresses continuing on the
following indented line.
"""
from __future__ import annotations

from core import aliases
from core.parsers import detect_kind, register
from core.parsers.labels import match_label_prefix
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
                    # them to a value that already has one is only safe if
                    # every renderer wraps identically - the moment one
                    # differs it manufactures a false shipper/consignee
                    # mismatch. Skipped here; the ONE case where a
                    # continuation line is read is handled below, where the
                    # label row carries no text at all.
                    continue

                if ":" in line:
                    label, _, value = line.partition(":")
                    label = label.strip()
                    value = value.strip()
                    field = aliases.field_for_label(label)
                else:
                    # Some documents align labels into columns instead of
                    # using a colon: "Shipper      APRIL FAR EAST (M) SDN BHD".
                    match = match_label_prefix(line.strip())
                    if match is None:
                        continue
                    field, label, value = match

                if field is None or field in fields:
                    # Unknown label, or the field is already captured - keep
                    # the FIRST occurrence, not the last.
                    continue

                raw, value_line = line, i

                if value == "":
                    # The row exists but carries NO text at all, which is how
                    # a document that puts the party block beneath its label
                    # renders. Take the first continuation line - the name -
                    # and never the whole address block, because appending
                    # addresses asymmetrically invents mismatches.
                    #
                    # A blank TOKEN ("???", "TBA") is deliberately excluded:
                    # that means the sender left the field empty on purpose
                    # and must stay blank so the email escalates as
                    # missing_value. `value == ""` is tested before is_blank()
                    # for exactly that reason.
                    nxt = lines[i] if i < len(lines) else ""
                    if nxt[:1].isspace() and nxt.strip():
                        value, raw, value_line = nxt.strip(), nxt, i + 1

                # A blank token ("???", "TBA", ...) means the row exists but
                # was left empty. Store it as "", never omit the field: a
                # missing key means "no such row", which is a different
                # escalation reason from "present but blank".
                stored_value = "" if aliases.is_blank(value) else value

                fields[field] = FieldValue(
                    value=stored_value,
                    raw=raw,
                    line_no=value_line,
                    label=label,
                    decided_by="rule",
                )

            return ExtractedDoc(path=path, kind=kind, fields=fields, text=text, readable=True)
        except Exception as exc:  # pragma: no cover - defensive; parse() must never raise
            return ExtractedDoc(path=path, kind="UNREADABLE", readable=False, error=str(exc))


register(TxtParser())
