import pytest

from core.types import FieldComparison, FieldValue
from core.variance import comparison_variance, explain_difference


@pytest.mark.parametrize(
    ("field", "si", "bl", "reason"),
    [
        ("consignee", "Alpha Trading Pte. Ltd.", "ALPHA TRADING PTE LTD", "punctuation_only"),
        ("shipper", "Ocean Paper Co Ltd", "OCEAN PAPER COMPANY LIMITED", "suffix_abbreviation"),
        ("notify_party", "Global Paper Trading Ltd", "TRADING GLOBAL PAPER LTD", "token_reorder"),
        ("consignee", "Pacific Pulp Sdn Bhd, Malaysia", "PACIFIC PULP SDN BHD", "country_suffix"),
    ],
)
def test_explains_controlled_formatting_variance(field, si, bl, reason):
    assert explain_difference(field, si, bl) == reason


@pytest.mark.parametrize(
    ("field", "si", "bl"),
    [
        ("gross_weight_kg", "22 MT", "22 KG"),
        ("container_count", "2 x 40HC", "1 x 40HC"),
        ("consignee", "ALPHA PAPER LTD", "BETA PAPER LTD"),
        ("port_of_loading", "SINGAPORE", "SHANGHAI"),
        ("shipper", "", "ALPHA LTD"),
    ],
)
def test_does_not_explain_substantive_or_numeric_differences(field, si, bl):
    assert explain_difference(field, si, bl) is None


def _fv(value):
    return FieldValue(value=value, raw=f"Consignee: {value}", line_no=1, label="Consignee")


def test_comparison_hint_never_changes_or_decorates_a_match():
    mismatch = FieldComparison(
        field="consignee",
        si=_fv("Ocean Paper Co Ltd"),
        bl=_fv("OCEAN PAPER COMPANY LIMITED"),
        si_norm="OCEAN PAPER CO LTD",
        bl_norm="OCEAN PAPER COMPANY LIMITED",
        matched=False,
    )
    assert comparison_variance(mismatch) == "suffix_abbreviation"
    assert mismatch.matched is False

    matched = FieldComparison(
        field=mismatch.field,
        si=mismatch.si,
        bl=mismatch.bl,
        si_norm=mismatch.si_norm,
        bl_norm=mismatch.bl_norm,
        matched=True,
    )
    assert comparison_variance(matched) is None
