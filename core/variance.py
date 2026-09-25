"""Conservative explanations for cosmetic-looking text differences.

These hints are presentation metadata only.  They never participate in
normalisation, comparison, or decision-making: a mismatch remains a mismatch
until a human reviews it (or deliberately teaches an exact equivalence).
"""
from __future__ import annotations

import re
from collections import Counter

from core.types import CompareField, FieldComparison

TEXT_FIELDS = frozenset({
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
})

_TOKENS = re.compile(r"[A-Z0-9]+")
_LEGAL_SUFFIXES = {
    "CO": "COMPANY",
    "CORP": "CORPORATION",
    "INC": "INCORPORATED",
    "LTD": "LIMITED",
    "INTL": "INTERNATIONAL",
}
_COUNTRY_SUFFIXES = (
    ("MALAYSIA",),
    ("SINGAPORE",),
    ("AUSTRALIA",),
    ("CHINA",),
    ("INDIA",),
    ("INDONESIA",),
    ("JAPAN",),
    ("THAILAND",),
    ("VIETNAM",),
    ("UAE",),
    ("UNITED", "ARAB", "EMIRATES"),
    ("UNITED", "KINGDOM"),
    ("UNITED", "STATES"),
)
#: "&"/"+" vs the spelled-out "AND" - a renderer choice, never content. Only
#: used to bridge that ONE substitution; it is not folded into _tokens()
#: itself so every other check keeps seeing "&"/"+" dropped as plain
#: punctuation (e.g. "A & B" vs "A B" is still punctuation_only, not this).
_AMPERSAND_PLUS = re.compile(r"[&+]")
#: Country name <-> short form, as they appear interchangeably on shipping
#: documents (NOT UN/LOCODE - that's the port table below). Used only to spot
#: "same city, country written differently"; matching entries in
#: _COUNTRY_SUFFIXES above stay untouched since that check answers a
#: different question (one side has no country at all).
_COUNTRY_VARIANTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("UAE",), ("UNITED", "ARAB", "EMIRATES")),
    (("PAK",), ("PAKISTAN",)),
    (("SG",), ("SINGAPORE",)),
    (("MY",), ("MALAYSIA",)),
)
#: Port name/city -> UN/LOCODE. Source: UN/LOCODE code list
#: (https://unece.org/trade/uncefact/cefact/locode), spelling variants as
#: seen on shipping documents. Deliberately small and explicit: every entry
#: here is one specific, auditable equivalence, never a fuzzy match. Ports
#: that are genuinely different - e.g. Dubai (AEDXB) and Jebel Ali (AEJEA),
#: both in the UAE - map to DIFFERENT codes on purpose and must never collapse
#: to the same hint.
_PORT_LOCODES: dict[str, str] = {
    "PORT KLANG": "MYPKG",
    "PORT KELANG": "MYPKG",
    "MYPKG": "MYPKG",
    "SINGAPORE": "SGSIN",
    "SGSIN": "SGSIN",
    "DUBAI": "AEDXB",
    "JEBEL ALI": "AEJEA",
    "HO CHI MINH CITY": "VNSGN",
    "HCMC": "VNSGN",
    "SAIGON": "VNSGN",
    "VNSGN": "VNSGN",
    "KARACHI": "PKKHI",
    "PKKHI": "PKKHI",
    "NANTONG": "CNNTG",
    "CNNTG": "CNNTG",
    "SHANGHAI": "CNSHA",
    "CNSHA": "CNSHA",
}


def _tokens(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return _TOKENS.findall(value.upper())


def _tokens_and_normalised(value: str) -> list[str]:
    """Tokens after "&"/"+" are spelled out as "AND" - see _AMPERSAND_PLUS."""
    return _TOKENS.findall(_AMPERSAND_PLUS.sub(" AND ", value.upper()))


def _expand_suffixes(tokens: list[str]) -> list[str]:
    return [_LEGAL_SUFFIXES.get(token, token) for token in tokens]


def _without_country_suffix(tokens: list[str]) -> "list[str] | None":
    for suffix in _COUNTRY_SUFFIXES:
        n = len(suffix)
        if len(tokens) > n and tuple(tokens[-n:]) == suffix:
            return tokens[:-n]
    return None


def _port_locode(tokens: list[str]) -> "str | None":
    """LOCODE for a port name, ignoring a trailing country name if present."""
    without_country = _without_country_suffix(tokens)
    base = without_country if without_country is not None else tokens
    return _PORT_LOCODES.get(" ".join(base))


def _country_variant_group(suffix: tuple[str, ...]) -> "tuple[str, ...] | None":
    for short, long in _COUNTRY_VARIANTS:
        if suffix == short or suffix == long:
            return long
    return None


def _split_country_variant(tokens: list[str]) -> "tuple[list[str], tuple[str, ...]] | None":
    """Split off a trailing country name/abbreviation known to _COUNTRY_VARIANTS.

    Longest suffix wins so "UNITED ARAB EMIRATES" isn't mistaken for a
    one-token match on "EMIRATES" alone (which isn't in the table anyway).
    """
    for n in (3, 2, 1):
        if len(tokens) > n:
            suffix = tuple(tokens[-n:])
            if _country_variant_group(suffix) is not None:
                return tokens[:-n], suffix
    return None


def explain_difference(field: CompareField, si_value: object, bl_value: object) -> "str | None":
    """Name a known formatting pattern, or return None when not provable."""
    if field not in TEXT_FIELDS or not isinstance(si_value, str) or not isinstance(bl_value, str):
        return None
    if not si_value.strip() or not bl_value.strip() or si_value == bl_value:
        return None

    si_tokens = _tokens(si_value)
    bl_tokens = _tokens(bl_value)
    if not si_tokens or not bl_tokens:
        return None

    if si_tokens == bl_tokens:
        return "punctuation_only"

    if _tokens_and_normalised(si_value) == _tokens_and_normalised(bl_value):
        return "ampersand_and"

    si_expanded = _expand_suffixes(si_tokens)
    bl_expanded = _expand_suffixes(bl_tokens)
    if si_expanded == bl_expanded:
        return "suffix_abbreviation"

    si_without_country = _without_country_suffix(si_expanded)
    bl_without_country = _without_country_suffix(bl_expanded)
    if si_without_country == bl_expanded or bl_without_country == si_expanded:
        return "country_suffix"

    si_locode = _port_locode(si_expanded)
    bl_locode = _port_locode(bl_expanded)
    if si_locode is not None and bl_locode is not None and si_locode == bl_locode:
        return "port_alias"

    si_country_variant = _split_country_variant(si_expanded)
    bl_country_variant = _split_country_variant(bl_expanded)
    if si_country_variant is not None and bl_country_variant is not None:
        si_base, si_suffix = si_country_variant
        bl_base, bl_suffix = bl_country_variant
        if si_base == bl_base and si_suffix != bl_suffix:
            return "country_variant"

    if Counter(si_expanded) == Counter(bl_expanded):
        return "token_reorder"
    return None


def comparison_variance(comparison: FieldComparison) -> "str | None":
    """Return a hint only for a decisive mismatch with both source values."""
    if comparison.matched or comparison.undecidable or comparison.si is None or comparison.bl is None:
        return None
    return explain_difference(comparison.field, comparison.si.value, comparison.bl.value)


#: The same wording as web/app.js's own VARIANCE_LABELS constant, so a hint
#: reads identically whichever runtime renders it - the "Try to fool it"
#: panel (api/_recheck.py, this dict) and a saved case's seam table
#: (web/app.js, its own copy). Duplicated deliberately: the two runtimes
#: share no module, so there is nowhere for one dict to live for both.
VARIANCE_LABELS: dict[str, str] = {
    "punctuation_only": "Punctuation differs",
    "suffix_abbreviation": "Possible company-suffix abbreviation",
    "token_reorder": "Same words, different order",
    "country_suffix": "Possible country suffix",
    "ampersand_and": "\"&\" vs \"AND\"",
    "port_alias": "Same port under another name - check and mark as same if correct",
    "country_variant": "Possible country name/abbreviation variant",
}


def variance_label(reason: "str | None") -> str:
    """The plain-English label for a variance kind, or the same generic
    fallback web/app.js's varianceLabel() uses for an unrecognised or
    missing reason. Never raises: an unknown key just falls back."""
    return VARIANCE_LABELS.get(reason or "", "Possible formatting variation")


__all__ = ["comparison_variance", "explain_difference", "VARIANCE_LABELS", "variance_label"]
