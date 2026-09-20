"""Label alias table: the document's words -> our field names.

OWNER: Sheng Kuan.

WHY THIS FILE EXISTS
--------------------
The SI and the BL label the same field differently. "Port of Loading" in one
document is "Load Port" in the other. This table is the answer to the third
bullet on the problem-statement slide.

The seed below covers every label the organiser's generator emits. That is
enough to score well on the provided data and NOT enough for the final round,
which runs on data we have not seen.

Sheng Kuan: add the aliases that real SI and BL documents use but this
generator does not. That is domain knowledge, not coding, and it is the single
highest-value thing you can contribute. Examples worth considering:
"Shipper/Consignor", "Receiver", "Also Notify", "Place of Receipt" (NOT the
same as port of loading - be careful), "Final Destination" (NOT the same as
port of discharge), "Total Packages", "Nett Weight" (NOT gross).

RULES
-----
1. Match on the NORMALISED label: casefolded, CJK stripped, punctuation and
   whitespace collapsed. `normalise_label()` below does this.
2. Longest alias wins. "Port of Loading (POL)" must beat "Port of Loading".
3. Never alias two different fields to the same string. Assert at import.
"""
from __future__ import annotations

import re
import unicodedata

from core.types import CompareField

#: Confirmed present in the organiser dataset (pools.LABELS).
ALIASES: dict[CompareField, tuple[str, ...]] = {
    "shipper": (
        "shipper",
        "shipper/exporter",
        "shipper (principal or seller)",
        "exporter",
    ),
    "consignee": (
        "consignee",
        "consignee (non-negotiable)",
        "to the order of",
        "buyer",
    ),
    "notify_party": (
        "notify party",
        "notify",
        "notify party/intermediate consignee",
    ),
    "port_of_loading": (
        "port of loading",
        "port of loading (pol)",
        "load port",
        "pol",
    ),
    "port_of_discharge": (
        "port of discharge",
        "port of discharge (pod)",
        "discharge port",
        "pod",
    ),
    "container_count": (
        "no. of containers",
        "total containers",
        "no. of containers or packages",
        "container count",
        "containers",
    ),
    "gross_weight_kg": (
        "gross weight (kg)",
        "gross wt (kgs)",
        "gross weight(kgs)",
        "gross weight",
        "total gross weight",
    ),
}

#: Values that mean "this field was left blank", not "this is the value".
#: Hitting one of these escalates to NEEDS_REVIEW / missing_value.
BLANK_TOKENS: frozenset[str] = frozenset(
    {"", "???", "_______", "____", "tba", "tbc", "n/a", "na", "-", "--", "____mt"}
)

_CJK = re.compile(r"[\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF]+")
_PUNCT = re.compile(r"[\s:\uFF1A]+")


def normalise_label(label: str) -> str:
    """Reduce a document label to its canonical comparable form.

    Steps, in order:
      1. NFKC, so full-width punctuation folds to ASCII
      2. drop CJK runs - the .docx renderer writes bilingual labels such as
         "Port of Loading (<chinese>)" and "Gross Weight (<chinese> KGS)"
      3. canonicalise brackets to " (inner)" with no padding, so that
         "Gross Weight ( KGS)" and "Gross Weight(KGS)" agree
      4. drop brackets left empty by step 2
      5. collapse whitespace, strip colons, casefold
    """
    s = unicodedata.normalize("NFKC", label)
    s = _CJK.sub(" ", s)
    s = _PUNCT.sub(" ", s)
    s = re.sub(r"\s*\(\s*", " (", s)
    s = re.sub(r"\s*\)", ")", s)
    s = re.sub(r"\s*\(\s*\)", "", s)
    s = re.sub(r"\s+", " ", s).strip().strip(":").strip()
    return s.casefold()


def _build_lookup() -> dict[str, CompareField]:
    lookup: dict[str, CompareField] = {}
    for fld, names in ALIASES.items():
        for name in names:
            key = normalise_label(name)
            if key in lookup and lookup[key] != fld:
                raise ValueError(
                    f"alias {name!r} maps to both {lookup[key]} and {fld}"
                )
            lookup[key] = fld
    return lookup


LOOKUP: dict[str, CompareField] = _build_lookup()

#: Longest first, so "port of loading (pol)" is tried before "port of loading".
ORDERED: tuple[str, ...] = tuple(sorted(LOOKUP, key=len, reverse=True))


def field_for_label(label: str) -> CompareField | None:
    """Exact match on the normalised label. Returns None when unknown."""
    return LOOKUP.get(normalise_label(label))


def is_blank(value: str) -> bool:
    return value.strip().casefold() in BLANK_TOKENS
