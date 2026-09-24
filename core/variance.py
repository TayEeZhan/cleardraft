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


def _tokens(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return _TOKENS.findall(value.upper())


def _expand_suffixes(tokens: list[str]) -> list[str]:
    return [_LEGAL_SUFFIXES.get(token, token) for token in tokens]


def _without_country_suffix(tokens: list[str]) -> "list[str] | None":
    for suffix in _COUNTRY_SUFFIXES:
        n = len(suffix)
        if len(tokens) > n and tuple(tokens[-n:]) == suffix:
            return tokens[:-n]
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

    si_expanded = _expand_suffixes(si_tokens)
    bl_expanded = _expand_suffixes(bl_tokens)
    if si_expanded == bl_expanded:
        return "suffix_abbreviation"

    si_without_country = _without_country_suffix(si_expanded)
    bl_without_country = _without_country_suffix(bl_expanded)
    if si_without_country == bl_expanded or bl_without_country == si_expanded:
        return "country_suffix"

    if Counter(si_expanded) == Counter(bl_expanded):
        return "token_reorder"
    return None


def comparison_variance(comparison: FieldComparison) -> "str | None":
    """Return a hint only for a decisive mismatch with both source values."""
    if comparison.matched or comparison.undecidable or comparison.si is None or comparison.bl is None:
        return None
    return explain_difference(comparison.field, comparison.si.value, comparison.bl.value)


__all__ = ["comparison_variance", "explain_difference"]
