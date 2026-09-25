"""Stage 3a support: parse a container-count string into its composition
(how many boxes, and of which sizes), and decide whether two compositions
genuinely agree.

OWNER: Sheng Kuan.

WHY this module exists
-----------------------
`container_count` used to be compared as a bare integer (core/normalise.py's
old normalise_container_count): "6" vs "6" match, "6" vs "5" mismatch, done.
That is wrong in three ways a bare int can never fix:

1. "2 x 40HC + 1 x 20GP" against the IDENTICAL string used to escalate to
   NEEDS_REVIEW, because the old rule refused to guess at mixed equipment
   at all - even when there was nothing to guess. Two identical strings are
   never ambiguous.
2. "6 x 40'HC" vs "6 x 20GP" used to be a silent OK: both total 6, so the
   bare-int comparison never looked at the size and called two shipments of
   roughly HALF the cargo volume the same.
3. "6 x 40'HC" vs a bare "6" used to be a silent OK too, even when nothing
   in the whole BL ever stated a size - the equipment was never actually
   verified.

This module is the fix: it parses a value into a `Composition` (a total plus,
where legible, the per-size breakdown) and `container_verdict` decides
"agree" / "differ" / "unverifiable" from BOTH sides' compositions and BOTH
documents' full text - never from a bare total alone.

Pure, no third-party imports, never raises - same house rules as
core/units.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_LEADING_INT = re.compile(r"\d+")

#: A container SIZE/TYPE written BEFORE the multiplier, e.g. "40' x 2" or
#: "40HC X 3" - the letter/apostrophe of the size token sits immediately
#: (whitespace aside) before the "x". In every case seen, the
#: quantity-first form ("2 x 40HC") is what a renderer actually means; the
#: type-first form is rare enough, and ambiguous enough about which number
#: is the quantity, that it is treated as unparseable rather than guessed
#: at. Deliberately excludes "*" - see _GROUP_TERM below.
_SIZE_BEFORE_MULTIPLIER = re.compile(r"[A-Za-z']\s*[xX×]\s*\d")

#: One "N x <size>" group, e.g. "2 x 40HC" -> ("2", "40HC"), "1 X 20GP" ->
#: ("1", "20GP"), "6 x 40'HC" -> ("6", "40'HC"). The size half requires two
#: digits immediately (whitespace aside) followed by a quote or a letter -
#: so it never fires on a bare weight or a quantity that happens to precede
#: unrelated numbers - and then greedily consumes the rest of the size
#: token (letters/digits/apostrophes) for the canonical group key.
#: "*" is deliberately NOT a multiplier here: unlike "x", it also turns up
#: as plain noise ("2 X 40 *"), and treating it as one would misread that
#: as a second group instead of trailing punctuation on the first.
_GROUP_TERM = re.compile(
    r"(\d+)\s*[xX×]\s*(\d{2}\s*(?:'|[A-Za-z])[A-Za-z0-9']*)"
)

#: Does a document mention a container SIZE anywhere (not just a bare
#: count)? Requires a size number (20/40/45) immediately followed - after
#: optional whitespace - by a foot mark or a recognised ISO/industry type
#: code, so an ordinary number never matches. Verified against weights:
#: "48,400 KG" and "22,040 KG" do not match, because nothing in either
#: string is one of 20/40/45 immediately followed by one of these tokens.
_SIZE_TOKEN = re.compile(
    r"\b(?:20|40|45)\s*(?:'|FT|GP|HC|HQ|FCL|DV|RF|RH|OT|FR|TK)", re.I
)


def _canonical_size(raw: str) -> str:
    """"40'HC" -> "40HC", "20 GP" -> "20GP": uppercase, drop the apostrophe
    and internal whitespace that never carry meaning.
    """
    return raw.upper().replace("'", "").replace(" ", "")


@dataclass(frozen=True, slots=True)
class Composition:
    """How many containers a value declares, and - where legible - the
    per-size breakdown.

    `groups` is None when the value never stated a size at all (a bare "6"),
    and a canonical, sorted tuple of (SIZE, count) pairs when it did, e.g.
    (("20GP", 1), ("40HC", 2)) for "2 x 40HC + 1 x 20GP".

    ``total`` alone is only ever safe to sum across multiple groups BECAUSE
    `container_verdict` below gates every comparison on the group breakdown
    first. Commit 15c49b1 deliberately removed summing for exactly this
    reason: "2 x 40HC + 1 x 20GP" (3 boxes) and "1 x 40HC + 2 x 20GP" (also
    3 boxes, split the OTHER way) must never silently match just because
    their totals agree. If anyone ever compares `.total` on its own again
    without going through `container_verdict`, that false match returns -
    see test_containers.py's pinned case for exactly this pair.
    """

    total: int
    groups: "tuple[tuple[str, int], ...] | None"


def parse_composition(text: str) -> "Composition | None":
    """Parse free-text container count into a Composition, or None when the
    quantity itself is unparseable/ambiguous. Never raises.
    """
    if not text:
        return None
    if _SIZE_BEFORE_MULTIPLIER.search(text):
        # Genuinely ambiguous which number is the quantity - never guessed.
        return None
    terms = _GROUP_TERM.findall(text)
    if terms:
        counts: dict[str, int] = {}
        for count_str, size_str in terms:
            size = _canonical_size(size_str)
            counts[size] = counts.get(size, 0) + int(count_str)
        groups = tuple(sorted(counts.items()))
        return Composition(total=sum(counts.values()), groups=groups)
    m = _LEADING_INT.match(text)
    if not m:
        return None
    return Composition(total=int(m.group()), groups=None)


def total_for(text: str) -> "int | None":
    """Composition.total for `text`, or None. Never raises."""
    comp = parse_composition(text)
    return None if comp is None else comp.total


def sizes_present_in(text: str) -> bool:
    """Does this DOCUMENT's full text mention any container size at all?
    Never raises.
    """
    if not text:
        return False
    return _SIZE_TOKEN.search(text) is not None


def container_verdict(si_value: object, bl_value: object, si_text: object, bl_text: object) -> str:
    """"agree" | "differ" | "unverifiable" for the container_count field.

    Rules, in order:
      1. either side's VALUE is unparseable -> "unverifiable"
      2. totals differ -> "differ"
      3. both sides have a group breakdown -> equal multiset ? "agree" : "differ"
      4. neither side has a group breakdown -> "agree" (same total, neither
         side stated a size to disagree about)
      5. exactly one side has a group breakdown -> the size is stated, just
         not in the count box on the OTHER document. Look at that other
         document's full TEXT: if it mentions a size anywhere, "agree" (the
         count and the equipment type simply sit in different boxes, as they
         do on most real Bills of Lading); otherwise "unverifiable" - the
         equipment was never actually confirmed.

    Never raises: any exception here is treated as "unverifiable" by the
    caller (core/compare.py wraps this call).
    """
    si_comp = parse_composition(str(si_value)) if si_value else None
    bl_comp = parse_composition(str(bl_value)) if bl_value else None

    if si_comp is None or bl_comp is None:
        return "unverifiable"
    if si_comp.total != bl_comp.total:
        return "differ"

    si_groups, bl_groups = si_comp.groups, bl_comp.groups
    if si_groups is not None and bl_groups is not None:
        return "agree" if si_groups == bl_groups else "differ"
    if si_groups is None and bl_groups is None:
        return "agree"

    # Exactly one side states a size breakdown. Check the OTHER document's
    # full text for a size mention anywhere.
    other_text = bl_text if si_groups is not None else si_text
    if sizes_present_in(str(other_text) if other_text else ""):
        return "agree"
    return "unverifiable"
