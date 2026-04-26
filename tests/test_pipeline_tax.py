import pytest

from pipeline.tax import estimate_annual_tax, load_tax_constants


def test_estimate_basic_no_exemption():
    # AV 30000 × eq 3.0027 = 90,081 EAV
    # 90,081 × 6.717% = 6,050.74
    tax = estimate_annual_tax(
        assessed_total=30000,
        equalizer=3.0027,
        composite_rate_pct=6.717,
        homeowner_exemption_eav_reduction=10000,
        has_homeowner_exemption=False,
    )
    assert round(tax, 2) == 6050.74


def test_estimate_with_homeowner_exemption():
    # EAV 90,081 − 10,000 = 80,081 taxable
    # 80,081 × 6.717% = 5,379.04
    tax = estimate_annual_tax(
        assessed_total=30000,
        equalizer=3.0027,
        composite_rate_pct=6.717,
        homeowner_exemption_eav_reduction=10000,
        has_homeowner_exemption=True,
    )
    assert round(tax, 2) == 5379.04


def test_estimate_returns_none_for_null_av():
    assert estimate_annual_tax(None, 3.0, 6.7, 10000, False) is None
    assert estimate_annual_tax(0, 3.0, 6.7, 10000, False) is None


def test_estimate_floors_at_zero_when_exemption_exceeds_eav():
    tax = estimate_annual_tax(
        assessed_total=1000,
        equalizer=3.0,
        composite_rate_pct=6.7,
        homeowner_exemption_eav_reduction=10000,
        has_homeowner_exemption=True,
    )
    assert tax == 0


def test_load_tax_constants_validates_required_keys(tmp_path):
    p = tmp_path / "incomplete.yaml"
    p.write_text("equalizer: 3.0\n")
    with pytest.raises(KeyError, match="composite_rate_pct"):
        load_tax_constants(p)


def test_load_tax_constants_returns_all_keys(tmp_path):
    p = tmp_path / "ok.yaml"
    p.write_text(
        "tax_year: 2024\n"
        "equalizer: 3.0027\n"
        "composite_rate_pct: 6.717\n"
        "homeowner_exemption_eav_reduction: 10000\n"
    )
    c = load_tax_constants(p)
    assert c["equalizer"] == 3.0027
    assert c["composite_rate_pct"] == 6.717
    assert c["homeowner_exemption_eav_reduction"] == 10000
