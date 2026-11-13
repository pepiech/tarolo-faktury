"""Scénář 5 (výpočty): více sazeb DPH, slevy, metody výpočtu a zaokrouhlení."""

from decimal import Decimal as D

import pytest

from invoicing.calc import LineInput, calculate


def L(qty, price, rate, discount="0"):  # noqa: N802
    return LineInput(D(qty), D(price), D(discount), D(rate))


def test_sample_invoice_vf2657():
    t = calculate([L("1", "1600.00", "21")])
    assert (t.total_base, t.total_vat, t.total) == (D("1600.00"), D("336.00"), D("1936.00"))
    assert [(r.rate_percent, r.base, r.vat) for r in t.vat_rows] == [(D("21"), D("1600.00"), D("336.00"))]


def test_multiple_rates_and_discount():
    t = calculate([L("2", "1000", "21", "10"), L("3", "99.90", "12"), L("1", "500", "0")])
    rows = {r.rate_percent: (r.base, r.vat) for r in t.vat_rows}
    assert rows[D("21")] == (D("1800.00"), D("378.00"))
    assert rows[D("12")] == (D("299.70"), D("35.96"))
    assert rows[D("0")] == (D("500.00"), D("0.00"))
    assert t.total == D("3013.66")


@pytest.mark.parametrize(
    "method, expected_vat", [("per_line", D("0.33")), ("per_rate_total", D("0.32"))]
)
def test_vat_methods_differ_on_rounding(method, expected_vat):
    # 3 × 0,51 s 21 %: po řádcích 0,11 × 3 = 0,33; ze součtu 1,53 × 21 % = 0,3213 → 0,32
    lines = [L("1", "0.51", "21")] * 3
    assert calculate(lines, method=method).total_vat == expected_vat


def test_whole_czk_rounding():
    t = calculate([L("1", "100.40", "21")], rounding_mode="whole_czk")
    assert t.total_before_rounding == D("121.48")
    assert t.rounding == D("-0.48")
    assert t.total == D("121.00")


def test_regime_without_vat_ignores_rates():
    t = calculate([L("1", "1000", "21")], applies_vat=False)
    assert t.total_vat == D("0.00")
    assert t.total == D("1000.00")
    assert t.vat_rows[0].rate_percent == D("0")


def test_half_up_rounding():
    assert calculate([L("1", "0.125", "0")]).total == D("0.13")


def test_deterministic():
    lines = [L("1.5", "333.33", "21"), L("2", "10", "12")]
    assert calculate(lines) == calculate(lines)
