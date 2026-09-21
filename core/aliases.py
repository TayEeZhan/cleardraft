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

#: DELIBERATELY NOT ALIASED. Each of these labels looks like one of our seven
#: fields and means something else. Mapping one feeds compare() a value from
#: the wrong row, and compare() trusts this table completely - so the email
#: comes back "MISMATCH" (or worse, "OK") with full confidence and a clerk is
#: told a correct Bill of Lading is wrong.
#:
#: A MISSING label costs one escalation to a human. A WRONG label costs the
#: trust the whole product is built on. When unsure, leave it out.
#:
#: tests/test_aliases.py pins every entry below to field_for_label() == None,
#: so a future "helpful" addition fails the suite instead of shipping.
#:
#:   place of receipt / pre-carriage from  inland pickup, NOT port of loading
#:   place of delivery / final destination inland dropoff, NOT port of discharge
#:   port of transhipment                  an intermediate port
#:   net weight / nett weight / n.w.       excludes packaging, NOT gross
#:   tare weight                           the empty container's own weight
#:   vgm / verified gross mass             cargo + container, a third number
#:   total packages / no. of cartons       cartons, NOT containers
#:   measurement / cbm                     volume, not weight or count
#:   also notify / 2nd notify party        a DIFFERENT company
#:   notify address                        may be an address, not a party name
#:
#: "Also Notify" and "Notify Address" appear as suggestions in the brief. They
#: are excluded on purpose: the first is a second party, and the second can be
#: a bare address, which would compare against the other document's company
#: name and manufacture a mismatch. Ask Averis before adding either.

#: Confirmed present in the organiser dataset (pools.LABELS), plus labels real
#: SI and BL documents use that this generator does not emit. The second group
#: is what has to carry us through the final round, which runs on unseen data.
ALIASES: dict[CompareField, tuple[str, ...]] = {
    "shipper": (
        # organiser dataset
        "shipper",
        "shipper/exporter",
        "shipper (principal or seller)",
        "exporter",
        # real-world
        "consignor",
        "shipper/consignor",
        "shipper name",
        "shipped by",
        "exporter/shipper",
        "shipper (exporter)",
    ),
    "consignee": (
        # organiser dataset
        "consignee",
        "consignee (non-negotiable)",
        "to the order of",
        "buyer",
        # real-world
        "consignee name",
        "consigned to",
        "receiver",
        "consignee/receiver",
        "to order of",
        "consignee (complete name and address)",
    ),
    "notify_party": (
        # organiser dataset
        "notify party",
        "notify",
        "notify party/intermediate consignee",
        # real-world. Only labels for the FIRST notify party: "also notify"
        # and "2nd notify party" are a different company - see the trap list.
        "notify party 1",
        "1st notify party",
        "first notify party",
        "notify party (complete name and address)",
        "notify applicant",
    ),
    "port_of_loading": (
        # organiser dataset
        "port of loading",
        "port of loading (pol)",
        "load port",
        "pol",
        # real-world. NOT "place of receipt" - that is the inland pickup.
        "loading port",
        "port of load",
        "port of shipment",
        "pol (port of loading)",
        "ocean port of loading",
    ),
    "port_of_discharge": (
        # organiser dataset
        "port of discharge",
        "port of discharge (pod)",
        "discharge port",
        "pod",
        # real-world. NOT "place of delivery" or "final destination".
        "discharging port",
        "port of unloading",
        "pod (port of discharge)",
        "discharge port (pod)",
        "ocean port of discharge",
    ),
    "container_count": (
        # organiser dataset
        "no. of containers",
        "total containers",
        "no. of containers or packages",
        "container count",
        "containers",
        # real-world. NOT "total packages" - cartons are not containers.
        "number of containers",
        "no of containers",
        "no. of container",
        "qty of containers",
        "quantity of containers",
        "container qty",
        "total container",
    ),
    "gross_weight_kg": (
        # organiser dataset
        "gross weight (kg)",
        "gross wt (kgs)",
        "gross weight (kgs)",
        "gross weight",
        "total gross weight",
        # real-world. NOT net/nett weight, tare weight or VGM.
        "gross wt",
        "gross wt.",
        "gross weight kgs",
        "gross weight in kg",
        "total gross wt",
        "total gross wt (kgs)",
        "g.w.",
        "g.w. (kgs)",
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
