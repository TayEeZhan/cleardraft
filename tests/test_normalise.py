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
