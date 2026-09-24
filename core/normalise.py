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
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

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
#: A container SIZE/TYPE written BEFORE the multiplier, e.g. "40' x 2" or
#: "40HC X 3" - the letter/apostrophe of the size token sits immediately
#: (whitespace aside) before the "x". In every case we have seen, the
#: quantity-first form ("2 x 40HC") is what a renderer actually means; the
#: type-first form is rare enough, and ambiguous enough about which number is
#: the quantity, that it is treated as unparseable rather than guessed at.
#: Deliberately excludes "*" - see _COUNT_TERM below.
_SIZE_BEFORE_MULTIPLIER = re.compile(r"[A-Za-z']\s*[xX×]\s*\d")
#: The quantity in an "N x <type>" group, e.g. the "2" in "2 x 40HC", the "1"
#: in "1 X 20GP". Matches only a digit run immediately (whitespace aside)
#: followed by "x"/"X"/"×" and then something size-shaped (two digits, then
#: a quote, "FT", or a letter - "20'", "40FT", "40HC"), so it never fires on
#: a bare weight or a quantity that happens to precede unrelated numbers.
#: "*" is deliberately NOT a multiplier here: unlike "x", it also turns up as
#: plain noise ("2 X 40 *"), and treating it as one would misread that as a
#: second group instead of trailing punctuation on the first.
_COUNT_TERM = re.compile(r"(\d+)\s*[xX×]\s*(?=\d{2}\s*(?:'|FT|[A-Za-z]))")
#: "21.577" is twenty-one thousand in European notation, not 21.577. Matches
#: 1-3 leading digits followed by one or more dot-delimited groups of exactly 3.
_DOT_THOUSANDS = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_COMMA_THOUSANDS = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_WEIGHT = re.compile(r"^(?P<number>\d+(?:[.,]\d+)*)\s*(?P<unit>[A-Za-z]+)?$")
_WEIGHT_MULTIPLIERS = {
    "KG": Decimal("1"),
    "KGS": Decimal("1"),
    "KGM": Decimal("1"),
    "MT": Decimal("1000"),
    "MTS": Decimal("1000"),
    "TON": Decimal("1000"),
    "TONNE": Decimal("1000"),
    "LB": Decimal("0.45359237"),
    "LBS": Decimal("0.45359237"),
}


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

    A wrong MATCH is the expensive error here, not a wrong escalation: a
    clerk reviewing an escalated case can still catch a real defect, but a
    silent match never gets a second look. So this function is deliberately
    conservative rather than clever about anything it cannot read with
    confidence, and fails closed to None (undecidable, per core/compare.py -
    a person checks) rather than guess:

    - A SIZE-first form ("40' x 2", "40HC X 3") is unparseable. Which number
      is the quantity is genuinely ambiguous when the size comes first, so
      this is never guessed at - always None.
    - A shipment split across more than one container type ("2 x 40HC + 1 x
      20GP") is also None, on purpose - NOT summed. Summing would make "2 x
      40HC + 1 x 20GP" (3 boxes, one split) equal "1 x 40HC + 2 x 20GP" (also
      3 boxes, split the other way), silently matching two shipments that
      are loaded completely differently. Mixed equipment escalates to a
      person instead.
    - Exactly one "N x <size>" group ("6 x 40'HC", "2x40HC", "10 X 40HC") is
      the unambiguous case: that single quantity is the count.
    - No "N x <size>" pattern at all falls back to the old leading-integer
      rule ("6", "6 CONTAINERS", and "2 X 40 *" where the trailing "*" is
      noise, not a second multiplier - see _COUNT_TERM above).
    """
    text = _as_text(value)
    if text is None:
        return None
    if _SIZE_BEFORE_MULTIPLIER.search(text):
        return None
    terms = _COUNT_TERM.findall(text)
    if len(terms) == 1:
        return int(terms[0])
    if len(terms) > 1:
        return None
    m = _LEADING_INT.match(text)
    if not m:
        return None
    return int(m.group())


def _weight_number(text: str) -> "Decimal | None":
    """Parse one locale-formatted number without discarding separators."""
    if "," in text and "." in text:
        # Whichever separator appears last is decimal; the other is thousands.
        if text.rfind(".") > text.rfind(","):
            text = text.replace(",", "")
        else:
            text = text.replace(".", "").replace(",", ".")
    elif _DOT_THOUSANDS.fullmatch(text):
        text = text.replace(".", "")
    elif _COMMA_THOUSANDS.fullmatch(text):
        text = text.replace(",", "")
    elif "," in text:
        if text.count(",") != 1:
            return None
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def normalise_weight(value: object) -> "int | None":
    """gross_weight_kg.

    Parse the numeric value and its unit as one value. Supported units are
    converted explicitly to kilograms; a bare value remains kilograms because
    the .xlsx renderer emits bare numeric cells. Unknown units fail closed to
    None, which core.compare turns into an undecidable row for human review.

    A decimal point must survive stripping. Deleting it turns "21,577.00 KGS"
    - a very common real-world rendering - into 2157700, which is a silent
    100x error that guarantees a false mismatch. Weights here differ by at
    least 500 kg when they genuinely differ, so rounding to the nearest
    kilogram is safe and both sides get the same treatment.
    """
    text = _as_text(value)
    if text is None:
        return None
    match = _WEIGHT.fullmatch(text)
    if match is None:
        return None
    number = _weight_number(match.group("number"))
    if number is None:
        return None
    unit = (match.group("unit") or "KG").upper()
    multiplier = _WEIGHT_MULTIPLIERS.get(unit)
    if multiplier is None:
        return None
    try:
        kilograms = number * multiplier
        return int(kilograms.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None
