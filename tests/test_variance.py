import re
from pathlib import Path

import pytest

from core.types import FieldComparison, FieldValue
from core.variance import VARIANCE_LABELS, comparison_variance, explain_difference, variance_label


@pytest.mark.parametrize(
    ("field", "si", "bl", "reason"),
    [
        ("consignee", "Alpha Trading Pte. Ltd.", "ALPHA TRADING PTE LTD", "punctuation_only"),
        ("shipper", "Ocean Paper Co Ltd", "OCEAN PAPER COMPANY LIMITED", "suffix_abbreviation"),
        ("notify_party", "Global Paper Trading Ltd", "TRADING GLOBAL PAPER LTD", "token_reorder"),
        ("consignee", "Pacific Pulp Sdn Bhd, Malaysia", "PACIFIC PULP SDN BHD", "country_suffix"),
        ("shipper", "Smith & Sons Trading", "SMITH AND SONS TRADING", "ampersand_and"),
        ("consignee", "Ocean + Paper Trading", "OCEAN AND PAPER TRADING", "ampersand_and"),
        ("port_of_loading", "PORT KLANG", "PORT KELANG", "port_alias"),
        ("port_of_discharge", "PORT KLANG, MALAYSIA", "MYPKG", "port_alias"),
        ("port_of_loading", "SINGAPORE", "SGSIN", "port_alias"),
        ("port_of_discharge", "HO CHI MINH CITY", "HCMC", "port_alias"),
        ("port_of_loading", "SAIGON", "VNSGN", "port_alias"),
        ("port_of_discharge", "KARACHI", "PKKHI", "port_alias"),
        ("port_of_loading", "NANTONG", "CNNTG", "port_alias"),
        ("port_of_discharge", "SHANGHAI", "CNSHA", "port_alias"),
        ("consignee", "Pacific Pulp Sdn Bhd, UAE", "PACIFIC PULP SDN BHD, UNITED ARAB EMIRATES", "country_variant"),
        ("shipper", "Karachi Textiles, Pak", "KARACHI TEXTILES, PAKISTAN", "country_variant"),
        ("notify_party", "Ocean Traders, SG", "OCEAN TRADERS, SINGAPORE", "country_variant"),
        ("consignee", "Pacific Pulp Sdn Bhd, MY", "PACIFIC PULP SDN BHD, MALAYSIA", "country_variant"),
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
        # Different ports, both in the UAE - must never collapse to the same
        # hint even though the country suffix matches.
        ("port_of_loading", "DUBAI", "JEBEL ALI"),
        ("port_of_discharge", "DUBAI, UAE", "JEBEL ALI, UAE"),
        # "&"/"+" resolved to "AND" is not the ONLY difference here.
        ("shipper", "Smith & Sons Trading", "SMITH AND DAUGHTERS TRADING"),
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


def test_variance_label_known_reasons_and_fallback():
    assert variance_label("port_alias") == (
        "Same port under another name - check and mark as same if correct"
    )
    assert variance_label("suffix_abbreviation") == "Possible company-suffix abbreviation"
    assert variance_label(None) == "Possible formatting variation"
    assert variance_label("not_a_real_reason") == "Possible formatting variation"


# ---------------------------------------------------------------------------
# web/app.js's own VARIANCE_LABELS is a hand-duplicated copy of this module's
# (there is no shared module between the two runtimes - see core/variance.py's
# comment above VARIANCE_LABELS). This test parses the JS object literal
# straight off disk and diffs it against the Python dict, so the two copies
# cannot silently drift apart the next time either one is edited alone.
# ---------------------------------------------------------------------------
def _js_variance_labels() -> dict[str, str]:
    app_js = Path(__file__).resolve().parent.parent / "web" / "app.js"
    text = app_js.read_text(encoding="utf-8")
    match = re.search(r"const VARIANCE_LABELS = \{(.*?)\};", text, re.S)
    assert match, "web/app.js: could not find `const VARIANCE_LABELS = { ... };`"
    body = match.group(1)
    pairs = re.findall(r'(\w+):\s*"((?:[^"\\]|\\.)*)"', body)
    assert pairs, "web/app.js: VARIANCE_LABELS block matched but no key/value pairs parsed"
    return {key: value.replace('\\"', '"').replace("\\\\", "\\") for key, value in pairs}


def test_js_and_python_variance_labels_match():
    assert _js_variance_labels() == VARIANCE_LABELS
