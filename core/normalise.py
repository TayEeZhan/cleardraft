"""Stage 3a: reduce two differently-formatted values to a comparable form.

OWNER: Ee Zhan.

This module decides half the score, so it states its reasoning.

EVERY planted defect in this dataset is SUBSTANTIVE, never cosmetic:
  entities -> a different company entirely
  ports    -> a different port entirely
  counts   -> differ by at least 1
  weights  -> differ by 500, 1000 or 2000 kg
(confirmed in the organiser generator, shipment.py:_mutate)

Therefore normalisation must be AGGRESSIVE about formatting and EXACT about
content. There is no case where a fuzzy threshold helps, and every fuzzy
threshold risks swallowing a real defect. We do not use one.
"""
from __future__ import annotations

import re

from core.aliases import is_blank
from core.types import CompareField

#: entity punctuation that varies across renderers but never carries meaning:
#: "MOORIM SP CO., LTD" vs "MOORIM SP CO LTD". Strip it everywhere, not just
#: at the end, since it can appear mid-string before a suffix like "LTD".
#: Does NOT include "*" - that is a continuation marker, not punctuation, and
#: is stripped separately by _ENTITY_CONTINUATION below.
_ENTITY_PUNCT = re.compile(r"[.,]")
#: Workshop finding: real shipping documents often truncate a party name with
#: a "*" or "**" continuation marker (the text continues elsewhere on the
#: page). A run of asterisks anywhere in the name - trailing, leading, or
#: wrapping it - is formatting noise from that truncation, never content, so
#: it is stripped before comparison like the entity punctuation above.
_ENTITY_CONTINUATION = re.compile(r"\*+")
#: Where a company name ends and its postal address begins. The renderers
#: disagree on the separator, so all of them are cut here.
_ENTITY_TAIL = re.compile(r"\s*[|\n\r]\s*")
#: A trailing bracket is stripped ONLY when it holds something LOCODE-shaped:
#: two letters then three alphanumerics, e.g. (MYPKG), (PECLL), (USNYC).
#: Stripping ANY trailing bracket would be a silent killer. "NEW YORK (APM
#: TERMINAL)" and "NEW YORK (RED HOOK TERMINAL)" are genuinely different
#: places, and collapsing them to "NEW YORK" would delete a real defect with
#: no error anywhere. Verified against the dataset: every trailing bracket in
#: a port field here is a 5-character UN/LOCODE, and non-trailing brackets
#: such as "PORT KLANG (WESTPORT), MALAYSIA (MYPKG)" are left untouched.
_TRAILING_LOCODE = re.compile(r"\s*\(\s*[A-Za-z]{2}[A-Za-z0-9]{3}\s*\)\s*$")
_WHITESPACE = re.compile(r"\s+")
_LEADING_INT = re.compile(r"\d+")
#: The quantity in an "N x <type>" group, e.g. the "2" in "2 x 40HC", the "1"
#: in "1 X 20GP". Matches only a digit run immediately (whitespace aside)
#: followed by one of the four multiplication signs seen in the dataset, so
#: it never fires on the container SIZE/TYPE itself ("40HC", "20GP", "45'HC"
#: contain no "x") or on a bare weight. One "N x" per group is all a group
#: ever has, so finding every occurrence anywhere in the text and summing
#: their quantities is equivalent to summing group-by-group, without having
#: to first split on the separators ("+", ",", "&", "and", a newline) that
#: can appear between groups.
_COUNT_TERM = re.compile(r"(\d+)\s*[xX×*]")
#: keep digits and both separators; strip units ("KG", "KGS", "MT") and spaces
_WEIGHT_NOISE = re.compile(r"[^\d.,]")
#: "21.577" is twenty-one thousand in European notation, not 21.577. Matches
#: 1-3 leading digits followed by one or more dot-delimited groups of exactly 3.
_DOT_THOUSANDS = re.compile(r"^\d{1,3}(?:\.\d{3})+$")


def _as_text(value: object) -> "str | None":
    """Coerce arbitrary input to a stripped string, or None if unusable/blank.

    Centralises the "never raise" guarantee: None, non-strings, and blank
    tokens (per aliases.is_blank) all collapse to None here so every
    normalise_* helper below can assume it has real text to work with.
    """
    if value is None:
        return None
    try:
        s = value if isinstance(value, str) else str(value)
    except Exception:
        return None
    s = s.strip()
    if is_blank(s):
        return None
    return s


def normalise(field: CompareField, value: str) -> "str | int | None":
    """Return the comparable form, or None when the value is blank."""
    text = _as_text(value)
    if text is None:
        return None
    try:
        if field in ("shipper", "consignee", "notify_party"):
            return normalise_entity(text)
        if field in ("port_of_loading", "port_of_discharge"):
            return normalise_port(text)
        if field == "container_count":
            return normalise_container_count(text)
        if field == "gross_weight_kg":
            return normalise_weight(text)
    except Exception:
        return None
    # Unknown field: nothing sensible to do, but never raise.
    return None


def normalise_entity(value: str) -> str:
    """shipper / consignee / notify_party.

    Uppercase, collapse whitespace, drop trailing punctuation, and drop "*"
    continuation markers ("EAST BRIGHT FZ-LLC *", "**UAB NOVAKOPA**") - a
    workshop finding that these mark a party name continuing elsewhere on the
    page, not a discrepancy. Keep the full legal name: "MOORIM SP CO., LTD"
    and "MOORIM SP CO LTD" must match, but "MOORIM SP" and "UAB NOVAKOPA"
    must not.
    """
    text = _as_text(value)
    if text is None:
        return ""
    # Compare the NAME only. Every renderer carries the postal address too,
    # but each separates it differently, so comparing the whole cell makes the
    # same company look like two:
    #     .xlsx  "KTP CO., LTD | KTP BLDG., 36 SANGWON-GIL; SEOUL"
    #     .docx  "KTP CO., LTD\nKTP BLDG., 36 SANGWON-GIL\nSEOUL"
    #     .txt   "KTP CO., LTD"          (address on a continuation line)
    # Cutting at the first separator gives all three the same answer. The
    # address stays in FieldValue.value, so the review screen can still show
    # the operator the full cell it was read from.
    s = _ENTITY_TAIL.split(text, 1)[0]
    s = s.upper()
    s = _ENTITY_PUNCT.sub("", s)
    s = _ENTITY_CONTINUATION.sub("", s)
    s = _WHITESPACE.sub(" ", s).strip()
    return s


def normalise_port(value: str) -> str:
    """port_of_loading / port_of_discharge.

    Strip the trailing UN/LOCODE in brackets. The .txt renderer writes
    "CALLAO, PERU (PECLL)" while the .pdf renderer writes "CALLAO, PERU" for
    the very same shipment. Comparing those raw produces a false defect.
    """
    text = _as_text(value)
    if text is None:
        return ""
    s = _TRAILING_LOCODE.sub("", text)
    s = s.upper()
    s = _WHITESPACE.sub(" ", s).strip()
    return s


def normalise_container_count(value: str) -> "int | None":
    """container_count.

    Documents write "6 x 40'HC". Only the 6 is compared - container SIZE is
    not one of the seven fields, and including it would invent defects.

    A shipment can span more than one container type: "2 x 40HC + 1 x 20GP".
    The comparable quantity is the TOTAL container count, so every "N x
    <type>" group is summed ("+", ",", "&", "and" and newlines all separate
    groups in the documents we have seen) - "2 x 40HC + 1 x 20GP" is 3, and
    "2 x 40HC + 3 x 20GP" is 5, so the two are correctly told apart instead
    of both collapsing to the leading "2". Container size/type is still
    discarded either way; only the quantities in front of each "x" are read.

    When the text has no "N x" pattern at all - a bare "6", "6 CONTAINERS" -
    this falls back to the old leading-integer rule.
    """
    text = _as_text(value)
    if text is None:
        return None
    terms = _COUNT_TERM.findall(text)
    if terms:
        try:
            return sum(int(term) for term in terms)
        except ValueError:
            return None
    m = _LEADING_INT.match(text)
    if not m:
        return None
    try:
        return int(m.group())
    except ValueError:
        return None


def normalise_weight(value: str) -> "int | None":
    """gross_weight_kg.

    Strip thousands separators and the KG/KGS suffix. The .xlsx renderer
    stores a bare number, the others store "21,577 KG". Return an int.

    A decimal point must survive stripping. Deleting it turns "21,577.00 KGS"
    - a very common real-world rendering - into 2157700, which is a silent
    100x error that guarantees a false mismatch. Weights here differ by at
    least 500 kg when they genuinely differ, so rounding to the nearest
    kilogram is safe and both sides get the same treatment.
    """
    text = _as_text(value)
    if text is None:
        return None
    s = _WEIGHT_NOISE.sub("", text)
    if not s:
        return None
    if _DOT_THOUSANDS.match(s):
        # European notation: the dots are thousands separators, not decimals.
        s = s.replace(".", "")
    s = s.replace(",", "")          # comma is a thousands separator here
    try:
        return int(round(float(s)))
    except (ValueError, OverflowError):
        return None
