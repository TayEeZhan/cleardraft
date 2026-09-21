"""PDF SI and BL parser.  OWNER: Sheng Kuan.

28 attachments. Four traps, all deliberate and all confirmed against the
real corpus:

  1. KIND DETECTION IS INVERTED IF TESTED NAIVELY. Every title in this
     corpus contains the substring "BILL OF LADING" - SI titles read
     "BILL OF LADING INSTRUCTION", BL titles read "BILL OF LADING (DRAFT)".
     You MUST test for "INSTRUCTION" first or every SI is misclassified as
     a BL.

  2. THE PER-CONTAINER WEIGHT TABLE HOLDS THE WRONG NUMBER. Each container
     row carries that container's own weight (e.g. 21,887 KG x 6 rows); the
     real gross_weight_kg is the line that begins "TOTAL " (e.g.
     "TOTAL Gross Wt (kgs): 131,322 KG"). The container rows are never
     matched here because they do not start with a known label. The "TOTAL "
     prefix is stripped (case-insensitive) before the alias lookup, since
     field_for_label("TOTAL Gross Wt (kgs)") itself resolves to nothing.

  3. MOST LABELS HAVE NO COLON. "Shipper APRIL FINE PAPER TRADING" is a
     label directly butted against its value with a single space, and the
     company's address wraps onto un-indented CONTINUATION lines directly
     below with no marker of its own. Strategy per line: try a colon split
     first; if there is no colon, walk aliases.ORDERED (longest-alias-first)
     and take the first one the line starts with, followed by a space or a
     colon (so "POL" cannot match "POLLUTION", and "Port of Discharge (POD)"
     is tried before the shorter "Port of Discharge"). A line that matches
     neither path is a continuation/address line and is ignored - its text
     is never appended to the field found on the line above.

  4. IMAGE-ONLY SCANS. Six PDFs in this corpus extract to empty text (no
     text layer at all) and two more are genuinely corrupt and raise
     PdfminerException on open. All eight come back readable=False,
     kind="UNREADABLE" - never a crash, never an empty "success".

Ports are written WITHOUT the UN/LOCODE here ("BUATAN, INDONESIA") while the
.txt renderer writes them WITH it. Do not fix that here - core/normalise.py
already strips the code on both sides. Return exactly what is on the page.
"""
from __future__ import annotations

import re

import pdfplumber

from core import aliases
from core.parsers import detect_kind, register
from core.types import CompareField, ExtractedDoc, FieldValue

#: Below this many characters of total extracted text, treat the PDF as an
#: image-only scan with no usable text layer.
_MIN_TEXT_CHARS = 40

#: A leading "TOTAL " (case-insensitive) on a label line, e.g.
#: "TOTAL Gross Wt (kgs): 131,322 KG". Stripped before the alias lookup.
_TOTAL_PREFIX = re.compile(r"^total\s+", re.IGNORECASE)

#: A PDF text-extraction glitch confirmed against the full 28-file corpus:
#: eight files render the "Gross Weight (KGS)" label as
#: "Gross Weightnn(KGS)" - e.g. "TOTAL Gross Weightnn(KGS): 143,940 KG" -
#: with the space before the bracket coming out as the literal characters
#: "nn" (a font/kerning artifact in that particular label's rendering).
#: Grepped across every extracted PDF in data/attachments: this exact
#: "<letter>nn(" pattern never occurs anywhere else, so restoring the space
#: here for the alias LOOKUP only (never touching the stored label/value)
#: carries no risk of corrupting unrelated text.
_GLUED_PAREN = re.compile(r"(?<=[A-Za-z])nn(?=\()")


def _match_known_prefix(line: str) -> "tuple[CompareField, str, str] | None":
    """The longest alias in aliases.ORDERED that `line` starts with.

    Requires the match to be followed by a space or a colon, so "POL" does
    not swallow a line that merely starts with those letters, and so a
    shorter alias never wins over a longer one that also matches (ORDERED is
    longest-first, and the first hit wins).
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
        label_text = line[:n]
        value_text = line[n:].lstrip(" :").strip()
        if _looks_interleaved(alias, value_text):
            # The label overflowed into the value column and the extractor
            # interleaved the glyphs. Returning "" marks the field blank, so
            # compare() calls the row undecidable instead of comparing
            # nonsense. Reporting a garbled string as a real value is how a
            # clerk gets told a correct Bill of Lading is wrong.
            return field, label_text, ""
        return field, label_text, value_text
    return None


#: How many characters of the label remainder must reappear at the start of
#: the value before we call the extraction interleaved. 8 is deliberate: at 4
#: the probe "part" (from "party/intermediate consignee") also matches the
#: genuine consignee "PARTNERS IN PAPER LLC".
_INTERLEAVE_PROBE = 8


def _looks_interleaved(matched_alias: str, value: str) -> bool:
    """True when the value begins with MORE OF ITS OWN LABEL.

    A long label that runs into the value column comes back with the two
    physically overlapping, and no text extractor can separate them. Real
    example from email_351_BL.pdf, where the label is
    "Notify Party/Intermediate Consignee" and the value is "KTP CO., LTD":

        'Notify Party/Intermediate ConsKiTgPne CeO., LTD'

    The short alias "notify" matches, and everything after it is garbage that
    still parses as a company name - a silent false mismatch.

    The signal is general, not tuned to that file: if the value starts with
    the remainder of some LONGER alias that also begins with the alias we
    matched, then we matched too short a label and the rest is corrupt.
    """
    if not value:
        return False
    head = value.casefold()
    for alias in aliases.ORDERED:
        if alias == matched_alias or not alias.startswith(matched_alias):
            continue
        remainder = alias[len(matched_alias):].strip()
        # 8 characters, not 4. At 4 the prefix "part" (from "party/...")
        # matched the real consignee "PARTNERS IN PAPER LLC" and blanked a
        # perfectly good value. The regression test pins that case.
        if len(remainder) >= _INTERLEAVE_PROBE and head.startswith(
            remainder[:_INTERLEAVE_PROBE]
        ):
            return True
    return False


#: Shortest alias allowed to END a value (i.e. to be treated as the start of
#: the next box on the same physical line). Six excludes "pol", "pod" and
#: "g.w.", which are short enough to appear inside a real value and would cut
#: it in half. A missed second box costs one escalation; a truncated first
#: value is a wrong answer that compare() trusts.
_MIN_SPLIT_ALIAS = 6

_WORD_START = re.compile(r"(?:^|(?<=\s))(\S+)")


def _split_candidates() -> dict[str, tuple[str, ...]]:
    """Aliases long enough to split a line, bucketed by their first word.

    Bucketing keeps the scan cheap: a word is only tested against the handful
    of aliases that begin with it, not against all of them.
    """
    buckets: dict[str, list[str]] = {}
    for alias in aliases.ORDERED:  # already longest-first
        if len(alias) < _MIN_SPLIT_ALIAS:
            continue
        buckets.setdefault(alias.split(" ", 1)[0], []).append(alias)
    return {word: tuple(found) for word, found in buckets.items()}


_SPLIT_ALIASES = _split_candidates()


def _find_column_break(value: str, already: CompareField) -> "int | None":
    """Index in `value` where a DIFFERENT field's label starts, or None.

    Real Bills of Lading print Shipper and Consignee in side-by-side boxes,
    and a text extractor emits both boxes on ONE physical line:

        Shipper ABC PAPER LTD Consignee XYZ TRADING LLC

    Taking the whole remainder as the shipper stores
    "ABC PAPER LTD Consignee XYZ TRADING LLC" - a value that is wrong rather
    than missing, and compare() trusts it completely. The generator's own PDFs
    are single-column, so nothing in the provided corpus exercises this; real
    carrier drafts do.
    """
    for match in _WORD_START.finditer(value):
        word = match.group(1).casefold().rstrip(":")
        for alias in _SPLIT_ALIASES.get(word, ()):
            end = match.start() + len(alias)
            if value[match.start():end].casefold() != alias:
                continue
            # Must be followed by a separator, or be the end of the line.
            if end < len(value) and value[end] not in (" ", ":"):
                continue
            field = aliases.field_for_label(alias)
            if field is None or field == already:
                continue
            return match.start()
    return None


#: A physical line holds a handful of boxes at most. The bound stops a
#: pathological line from looping.
_MAX_BOXES_PER_LINE = 4


def _extract_fields(line: str) -> "list[tuple[CompareField, str, str]]":
    """Every field on one physical line, left to right.

    One box per line is the common case and returns a single entry. A line
    holding two boxes returns both, with the first value cut at the second
    box's label instead of swallowing it.
    """
    out: list[tuple[CompareField, str, str]] = []
    rest = line.strip()
    seen: set[CompareField] = set()

    for _ in range(_MAX_BOXES_PER_LINE):
        match = _extract_field(rest)
        if match is None:
            break
        field, label, value = match

        cut = _find_column_break(value, field)
        if cut is None:
            rest = ""
        else:
            rest = value[cut:]
            value = value[:cut].strip()

        if field not in seen:
            seen.add(field)
            out.append((field, label, value))
        if not rest:
            break

    return out


def _extract_field(line: str) -> "tuple[CompareField, str, str] | None":
    """Resolve one physical line to (field, label, value), or None.

    Colon split first; a bare-prefix match second. Both are tried against
    the line with any leading "TOTAL " already removed, so
    "TOTAL Gross Wt (kgs): 131,322 KG" resolves the same as
    "Gross Wt (kgs): 131,322 KG" would.
    """
    stripped = line.strip()
    if not stripped:
        return None

    searchable = _TOTAL_PREFIX.sub("", stripped)

    if ":" in searchable:
        label, _, value = searchable.partition(":")
        label = label.strip()
        value = value.strip()
        field = aliases.field_for_label(label)
        if field is None:
            # Retry with the glued-space glitch repaired. The stored label
            # stays exactly as printed; only the lookup key is repaired.
            field = aliases.field_for_label(_GLUED_PAREN.sub(" ", label))
        if field is not None:
            return field, label, value

    return _match_known_prefix(searchable)


class PdfParser:
    extensions = (".pdf",)

    def parse(self, path: str) -> ExtractedDoc:
        try:
            with pdfplumber.open(path) as pdf:
                page_texts = [page.extract_text() or "" for page in pdf.pages]
        except Exception as exc:
            return ExtractedDoc(path=path, kind="UNREADABLE", readable=False, error=str(exc))

        try:
            text = "\n".join(page_texts)

            if len(text.strip()) < _MIN_TEXT_CHARS:
                return ExtractedDoc(
                    path=path, kind="UNREADABLE", readable=False, error="no text layer",
                )

            lines = text.splitlines()

            title = ""
            for line in lines:
                if line.strip():
                    title = line.strip()
                    break
            kind = detect_kind(title)

            fields: dict[CompareField, FieldValue] = {}
            for i, line in enumerate(lines, start=1):
                # One physical line can carry two side-by-side boxes.
                for field, label, value in _extract_fields(line):
                    if field in fields:
                        # Keep the FIRST occurrence, not the last.
                        continue

                    stored_value = "" if aliases.is_blank(value) else value

                    fields[field] = FieldValue(
                        value=stored_value,
                        raw=line.strip(),
                        line_no=i,
                        label=label,
                        decided_by="rule",
                    )

            return ExtractedDoc(path=path, kind=kind, fields=fields, text=text, readable=True)
        except Exception as exc:
            return ExtractedDoc(path=path, kind="UNREADABLE", readable=False, error=str(exc))


register(PdfParser())
