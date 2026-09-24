from __future__ import annotations

from core.normalise import (
    normalise,
    normalise_container_count,
    normalise_entity,
    normalise_port,
    normalise_weight,
)


def test_port_locode_is_formatting_not_content() -> None:
    assert normalise_port("CALLAO, PERU (PECLL)") == normalise_port("CALLAO, PERU")


def test_container_count_discards_size_but_not_quantity() -> None:
    assert normalise_container_count("6 x 40'HC") == 6
    assert normalise_container_count("1 x 20GP") == 1
    assert normalise_container_count("3 x 40'HC") != normalise_container_count(
        "4 x 40'HC"
    )


def test_container_count_plain_values() -> None:
    assert normalise_container_count("6") == 6
    assert normalise_container_count("6 CONTAINERS") == 6
    assert normalise_container_count("") is None
    assert normalise_container_count(None) is None


def test_container_count_sums_mixed_groups() -> None:
    # A shipment split across two container types is a TOTAL, not the
    # leading group's quantity alone - both used to normalise to 2.
    assert normalise_container_count("2 x 40HC + 1 x 20GP") == 3
    assert normalise_container_count("2 x 40HC + 3 x 20GP") == 5
    # A real defect (3 vs 5) must not be swallowed by only reading the
    # leading integer.
    assert normalise_container_count(
        "2 x 40HC + 1 x 20GP"
    ) != normalise_container_count("2 x 40HC + 3 x 20GP")


def test_container_count_same_totals_match() -> None:
    # Different splits that add up the same are the same total quantity.
    assert normalise_container_count("2 x 40HC + 2 x 20GP") == normalise_container_count(
        "3 x 40HC + 1 x 20GP"
    )


def test_container_count_accepts_comma_separated_groups() -> None:
    assert normalise_container_count("1 X 20GP, 2 X 40HC") == 3


def test_weight_accepts_formatted_and_raw_xlsx_values() -> None:
    assert normalise_weight("21,577 KG") == 21577
    assert normalise_weight("21577") == 21577
    assert normalise_weight(21577) == 21577


def test_entity_punctuation_is_formatting_not_content() -> None:
    assert normalise_entity("MOORIM SP CO., LTD") == normalise_entity(
        "MOORIM SP CO LTD"
    )


def test_distinct_entities_do_not_collapse() -> None:
    assert normalise_entity("MOORIM SP CO., LTD") != normalise_entity("UAB NOVAKOPA")


def test_entity_continuation_marker_is_formatting_not_content() -> None:
    # Workshop finding: a trailing/wrapping "*" or "**" marks a party name
    # that continues elsewhere on the page - not a discrepancy.
    assert normalise_entity("EAST BRIGHT FZ-LLC *") == normalise_entity(
        "EAST BRIGHT FZ-LLC"
    )
    assert normalise_entity("**UAB NOVAKOPA**") == normalise_entity("UAB NOVAKOPA")
    assert normalise_entity("MOORIM SP CO., LTD *") == normalise_entity(
        "MOORIM SP CO LTD"
    )


def test_entity_continuation_marker_does_not_mask_real_defects() -> None:
    assert normalise_entity("UAB NOVAKOPA *") != normalise_entity("EAST BRIGHT FZ-LLC")


def test_entity_extra_spacing_is_formatting_not_content() -> None:
    assert normalise_entity("KTP  CO.,   LTD") == normalise_entity("KTP CO., LTD")


def test_blank_required_values_are_undecidable() -> None:
    for token in ("", "???", "_______", "TBA", "TBC", "N/A"):
        assert normalise("shipper", token) is None
        assert normalise("gross_weight_kg", token) is None
