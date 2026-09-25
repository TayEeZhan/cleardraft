"""Stage 3a support: parse a weight-with-unit string into a comparable value.

OWNER: Sheng Kuan.

core.normalise's weight handling used to strip every non-digit character before
comparing two weights - including the unit. That silently treats "22 MT" and "22 KG"
as the same number (22) even though they differ by a factor of 1000, and conversely
flags "22 MT" and "22,000 KG" - the SAME weight - as a mismatch because 22 != 22000.
Both directions are wrong, and both are silent: normalise_weight returned a clean int
either way, so nothing downstream ever saw an error.

This module makes the unit part of the value instead of noise to be deleted. It
converts every supported unit to kilograms before the two sides of a shipment are ever
compared, so a genuine 1000x/2.2x mislabelling shows up as a real difference, and a
same-weight-different-unit case does not show up as one.

"TON" (singular or plural) and uppercase "T" are deliberately NOT converted. A US
short ton is 907.18 kg, a UK long ton is 1016.05 kg, and a metric tonne is
1000 kg - three different weights that happen to share a word. In freight, a
"revenue/measurement ton" is a different concept again (1 cubic metre or
1000 kg, whichever is greater). Nothing printed on a shipping document
distinguishes which one is meant, so TON/TONS/T all fail to parse and the row
escalates for a human to resolve rather than guessing which conversion the
shipper intended. Lowercase "t" is different: it is the internationally
standardised symbol for the tonne, exactly 1000 kg, so it is accepted alongside
MT/MTS/TONNE/TONNES.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

#: number part: digits plus the two separators seen in this dataset ("21,577",
#: "21.577", "22.5").
_NUMBER = r"[\d.,]+"
#: unit part: letters plus "." and "/" as seen in the wild ("KGS.", "M/T"). Optional -
#: a bare number is kilograms.
_UNIT = r"[A-Za-z./]*"
#: the whole value must be number-then-unit and nothing else, so trailing junk like
#: "22 KG net" is rejected rather than silently truncated.
_SHAPE = re.compile(rf"^\s*({_NUMBER})\s*({_UNIT})\s*$")

#: "21.577" is twenty-one thousand in European notation, not 21.577. Copied from
#: core.normalise's old _DOT_THOUSANDS so number parsing behaves identically.
_DOT_THOUSANDS = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_COMMA_THOUSANDS = re.compile(r"^\d{1,3}(?:,\d{3})+$")

#: canonical unit -> factor to multiply the parsed number by to get kilograms.
#: EXACTLY these tokens - see module docstring for why uppercase T/TONS/QTL are
#: absent and lowercase t is present.
#:
#: MT/MTS/TONNE/TONNES are exact at x1000 - that is the realistic case in this
#: dataset (every planted weight defect is a whole MT/KG relabelling). LB/LBS are
#: NOT exact at kilogram precision: 1 lb = 0.45359237 kg exactly, but rounding the
#: product to the nearest kilogram is still lossy for most inputs. For example
#: 48,500 LBS -> 21999.229945 kg -> rounds to 21999, one kilogram away from
#: 22,000 KG even though a human would call them "the same weight". We do not
#: paper over that with a tolerance: core/normalise.py:14-16 rules out fuzzy
#: thresholds precisely because every fuzzy threshold risks swallowing a real
#: defect, so a 1 kg pound-rounding gap escalates like any other difference
#: instead of being silently forgiven.
_FACTORS: dict[str, Decimal] = {
    "": Decimal("1"),
    "KG": Decimal("1"), "KGS": Decimal("1"), "KGM": Decimal("1"),
    "KILO": Decimal("1"), "KILOS": Decimal("1"),
    "KILOGRAM": Decimal("1"), "KILOGRAMS": Decimal("1"),
    "t": Decimal("1000"),
    "MT": Decimal("1000"), "MTS": Decimal("1000"),
    "TONNE": Decimal("1000"), "TONNES": Decimal("1000"),
    "LB": Decimal("0.45359237"), "LBS": Decimal("0.45359237"),
    "POUND": Decimal("0.45359237"), "POUNDS": Decimal("0.45359237"),
}
#: Two values are only a UNIT discrepancy when their conversion factors differ.
#: Comparing the factor rather than the token means every alternative spelling of
#: one unit - "MT"/"MTS"/"TONNE", "LBS"/"POUNDS", "KG"/bare - is correctly treated
#: as the same unit. Those pairs carry identical numbers as well, so flagging them
#: would put a "units differ" note on a row where nothing differs at all.


@dataclass(frozen=True, slots=True)
class Weight:
    kg: int      # rounded to the nearest kilogram AFTER conversion
    unit: str    # canonical token; official tonne symbol stays "t", others uppercase


def _coerce_text(value: object) -> "str | None":
    """str/int/float -> stripped text, or None. Deliberately standalone (does not
    import core.aliases.is_blank) so this module has zero dependencies on the rest
    of core and can be used/tested in isolation.
    """
    if value is None:
        return None
    try:
        s = value if isinstance(value, str) else str(value)
    except Exception:
        return None
    s = s.strip()
    return s or None


def _canonical_unit(raw: str) -> str:
    """Normalise unit spelling without destroying the case-sensitive tonne symbol.

    The SI symbol ``t`` means exactly one metric tonne. Uppercase ``T`` is not
    that symbol and remains unsupported. Other accepted shipping units remain
    case-insensitive (``m/t`` -> ``MT``, ``KGS.`` -> ``KGS``).
    """
    cleaned = raw.replace(".", "").replace("/", "").replace(" ", "")
    if cleaned == "t":
        return "t"
    return cleaned.upper()


def _parse_number(text: str) -> "Decimal | None":
    """Parse US or European separators without deleting decimal precision."""
    if "," in text and "." in text:
        # The final separator is decimal; the other is a thousands separator.
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


def parse_weight(value: object) -> "Weight | None":
    """Parse free-text weight into kilograms + its written unit. Never raises:
    unparseable input, an unsupported unit, or trailing junk after the unit
    (e.g. "22 KG net") all return None.
    """
    text = _coerce_text(value)
    if text is None:
        return None
    m = _SHAPE.match(text)
    if not m:
        return None
    number_part, unit_part = m.group(1), m.group(2)
    unit = _canonical_unit(unit_part)
    factor = _FACTORS.get(unit)
    if factor is None:
        return None
    number = _parse_number(number_part)
    if number is None:
        return None
    try:
        kilograms = number * factor
        kg = int(kilograms.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return Weight(kg=kg, unit=unit)


def to_kilograms(value: object) -> "int | None":
    """parse_weight(value).kg, or None. Never raises."""
    w = parse_weight(value)
    return None if w is None else w.kg


def describe_unit_difference(si_value: object, bl_value: object) -> "str | None":
    """Describe a SAME-weight-different-UNIT pair, or None otherwise. Returns
    "<unit A> vs <unit B>" only when both sides parse, resolve to the identical
    number of kilograms, and are written in genuinely different units - not just a
    different spelling of kilograms (a bare number already means kilograms).
    Never raises.
    """
    wa = parse_weight(si_value)
    wb = parse_weight(bl_value)
    if wa is None or wb is None:
        return None
    if wa.kg != wb.kg:
        return None
    if _FACTORS[wa.unit] == _FACTORS[wb.unit]:
        # Same unit, possibly spelled differently ("MT" vs "TONNES", "KG" vs a
        # bare number). Equal factors plus equal kilograms means the two numbers
        # on the page are identical too, so there is nothing for a person to look
        # at - a note here would be noise on an ordinary clean row.
        return None
    return f"{wa.unit or 'KG'} vs {wb.unit or 'KG'}"
