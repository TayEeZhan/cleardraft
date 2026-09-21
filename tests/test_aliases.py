"""The alias table: what must map, and what must NEVER map.

OWNER: Sheng Kuan.

The trap tests are the important half of this file. compare() trusts the
alias table completely, so a label mapped to the wrong field produces a
confident wrong answer - the one outcome this product promises to avoid. A
label left unmapped only costs one escalation to a human, which is safe.

These tests exist so that a future "helpful" addition of "Nett Weight" or
"Total Packages" fails the suite instead of reaching a clerk.
"""
from __future__ import annotations

import pytest

from core import aliases

# --------------------------------------------------------------------------
# Traps: look like one of our seven fields, mean something else.
# --------------------------------------------------------------------------
TRAPS: tuple[tuple[str, str], ...] = (
    # inland legs of the journey, not the sea ports we compare
    ("Place of Receipt", "inland pickup, not the port of loading"),
    ("Pre-carriage from", "inland leg before the vessel"),
    ("Place of Delivery", "inland dropoff, not the port of discharge"),
    ("Final Destination", "inland final stop, not the port of discharge"),
    ("Port of Transhipment", "an intermediate port"),
    ("Port of Transshipment", "an intermediate port, US spelling"),
    # other weights on the same document
    ("Net Weight", "excludes packaging"),
    ("Nett Weight", "excludes packaging, common spelling"),
    ("Net Wt (kgs)", "excludes packaging"),
    ("N.W.", "abbreviation for net weight"),
    ("Tare Weight", "the empty container's own weight"),
    ("VGM", "verified gross mass: cargo plus container"),
    ("Verified Gross Mass", "cargo plus container, a third number"),
    # counts of something other than containers
    ("Total Packages", "cartons are not containers"),
    ("No. of Packages", "cartons are not containers"),
    ("Number of Packages", "cartons are not containers"),
    ("No. of Cartons", "cartons are not containers"),
    ("Total Pallets", "pallets are not containers"),
    # not a weight or a count at all
    ("Measurement", "volume in cubic metres"),
    ("CBM", "volume in cubic metres"),
    # a different company from the notify party
    ("Also Notify", "a second, different party"),
    ("2nd Notify Party", "a second, different party"),
    ("Second Notify Party", "a second, different party"),
    # an address, which would be compared against a company name
    ("Notify Address", "may hold an address, not a party name"),
)


@pytest.mark.parametrize("label,why", TRAPS)
def test_trap_labels_are_never_mapped(label: str, why: str) -> None:
    assert aliases.field_for_label(label) is None, (
        f"{label!r} must stay unmapped ({why}). Mapping it feeds compare() a "
        f"value from the wrong row and it will be trusted."
    )


@pytest.mark.parametrize("label,why", TRAPS)
def test_trap_labels_survive_case_and_punctuation(label: str, why: str) -> None:
    """The traps must not sneak back in through normalisation."""
    assert aliases.field_for_label(label.upper()) is None, why
    assert aliases.field_for_label(f"  {label} :") is None, why


# --------------------------------------------------------------------------
# Labels the organiser's own documents use. Regression pins: if one of these
# stops resolving, extraction silently loses a field on the provided data.
# --------------------------------------------------------------------------
DATASET_LABELS: tuple[tuple[str, str], ...] = (
    ("Shipper", "shipper"),
    ("Shipper/Exporter", "shipper"),
    ("Shipper (Principal or Seller)", "shipper"),
    ("Consignee", "consignee"),
    ("Consignee (Non-Negotiable)", "consignee"),
    ("To the Order of", "consignee"),
    ("Notify", "notify_party"),
    ("Notify Party", "notify_party"),
    ("Notify Party/Intermediate Consignee", "notify_party"),
    ("Port of Loading", "port_of_loading"),
    ("Port of Loading (POL)", "port_of_loading"),
    ("Load Port", "port_of_loading"),
    ("POL", "port_of_loading"),
    ("Port of Discharge", "port_of_discharge"),
    ("Discharge Port", "port_of_discharge"),
    ("POD", "port_of_discharge"),
    ("No. of Containers", "container_count"),
    ("Total Containers", "container_count"),
    ("Container Count", "container_count"),
    ("Gross Weight (KG)", "gross_weight_kg"),
    ("Gross Wt (kgs)", "gross_weight_kg"),
    ("GROSS WEIGHT", "gross_weight_kg"),
    ("TOTAL Gross Weight", "gross_weight_kg"),
    # bilingual .docx labels: normalise_label strips the CJK run
    ("Port of Loading (装货港)", "port_of_loading"),
    ("Gross Weight毛重(KGS)", "gross_weight_kg"),
)


@pytest.mark.parametrize("label,field", DATASET_LABELS)
def test_dataset_labels_still_resolve(label: str, field: str) -> None:
    assert aliases.field_for_label(label) == field


# --------------------------------------------------------------------------
# Labels real documents use that the organiser's generator never emits. These
# are the additions that have to carry the final round.
# --------------------------------------------------------------------------
REAL_WORLD_LABELS: tuple[tuple[str, str], ...] = (
    ("Consignor", "shipper"),
    ("Shipper/Consignor", "shipper"),
    ("Shipped by", "shipper"),
    ("Consignee Name", "consignee"),
    ("Consigned to", "consignee"),
    ("Receiver", "consignee"),
    ("Notify Party 1", "notify_party"),
    ("1st Notify Party", "notify_party"),
    ("Loading Port", "port_of_loading"),
    ("Port of Shipment", "port_of_loading"),
    ("Ocean Port of Loading", "port_of_loading"),
    ("Discharging Port", "port_of_discharge"),
    ("Port of Unloading", "port_of_discharge"),
    ("Number of Containers", "container_count"),
    ("Qty of Containers", "container_count"),
    ("Container Qty", "container_count"),
    ("Gross Wt", "gross_weight_kg"),
    ("Gross Weight in KG", "gross_weight_kg"),
    ("G.W. (KGS)", "gross_weight_kg"),
)


@pytest.mark.parametrize("label,field", REAL_WORLD_LABELS)
def test_real_world_labels_resolve(label: str, field: str) -> None:
    assert aliases.field_for_label(label) == field


# --------------------------------------------------------------------------
# Table-level invariants
# --------------------------------------------------------------------------
def test_no_label_maps_to_two_fields() -> None:
    """_build_lookup() raises on a collision; this states the rule out loud."""
    seen: dict[str, str] = {}
    for field, labels in aliases.ALIASES.items():
        for label in labels:
            key = aliases.normalise_label(label)
            assert key not in seen or seen[key] == field, (
                f"{label!r} maps to both {seen.get(key)} and {field}"
            )
            seen[key] = field


def test_aliases_are_stored_already_normalised() -> None:
    """Keeps the table readable and the longest-first ordering honest."""
    for field, labels in aliases.ALIASES.items():
        for label in labels:
            assert label == aliases.normalise_label(label), (
                f"{label!r} in {field} is not in normalised form"
            )


def test_longer_alias_wins() -> None:
    """ORDERED is longest-first so a prefix match cannot take a short alias."""
    lengths = [len(a) for a in aliases.ORDERED]
    assert lengths == sorted(lengths, reverse=True)


def test_blank_tokens() -> None:
    for token in ("", "???", "TBA", "tbc", "N/A", "-", "_______"):
        assert aliases.is_blank(token), token
    for value in ("MOORIM SP CO., LTD", "0", "6 x 40'HC", "21577"):
        assert not aliases.is_blank(value), value
