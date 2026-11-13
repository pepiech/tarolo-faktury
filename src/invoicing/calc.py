"""Deterministický výpočet faktury. Jediný zdroj pravdy pro návrh, náhled, PDF i vystavení.

Pořadí výpočtu (technický návrh, kap. 6.1):
1. základ řádku = round2(množství × cena × (1 − sleva/100))
2. DPH řádku   = round2(základ × sazba/100), pokud režim DPH počítá daň, jinak 0
3. rekapitulace po sazbách:
   - per_line:       součet základů a součet DPH řádků
   - per_rate_total: součet základů, DPH = round2(součet × sazba/100)
4. celkem = Σ základ + Σ DPH; zaokrouhlení podle režimu; k úhradě = celkem + zaokrouhlení
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")


def round2(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def to_decimal(value, default=ZERO):
    if value is None or value == "":
        return default
    return Decimal(str(value))


@dataclass(frozen=True)
class LineInput:
    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal = ZERO
    vat_rate_percent: Decimal = ZERO


@dataclass(frozen=True)
class LineResult:
    base: Decimal
    vat: Decimal
    total: Decimal
    vat_rate_percent: Decimal


@dataclass(frozen=True)
class VatRow:
    rate_percent: Decimal
    base: Decimal
    vat: Decimal

    @property
    def total(self):
        return self.base + self.vat

    def as_dict(self):
        return {
            "rate_percent": str(self.rate_percent.normalize()),
            "base": str(self.base),
            "vat": str(self.vat),
            "total": str(self.total),
        }


@dataclass(frozen=True)
class Totals:
    lines: list = field(default_factory=list)
    vat_rows: list = field(default_factory=list)
    total_base: Decimal = ZERO
    total_vat: Decimal = ZERO
    total_before_rounding: Decimal = ZERO
    rounding: Decimal = ZERO
    total: Decimal = ZERO  # částka k úhradě


def calculate(lines, *, applies_vat=True, method="per_line", rounding_mode="none"):
    results = []
    groups = {}
    for line in lines:
        quantity = to_decimal(line.quantity)
        unit_price = to_decimal(line.unit_price)
        discount = to_decimal(line.discount_percent)
        rate = to_decimal(line.vat_rate_percent) if applies_vat else ZERO
        base = round2(quantity * unit_price * (1 - discount / HUNDRED))
        vat = round2(base * rate / HUNDRED)
        results.append(LineResult(base=base, vat=vat, total=base + vat, vat_rate_percent=rate))
        group = groups.setdefault(rate, {"base": ZERO, "vat": ZERO})
        group["base"] += base
        group["vat"] += vat

    vat_rows = []
    for rate in sorted(groups, reverse=True):
        base = groups[rate]["base"]
        if method == "per_rate_total":
            vat = round2(base * rate / HUNDRED)
        elif method == "per_line":
            vat = groups[rate]["vat"]
        else:
            raise ValueError(f"Neznámá metoda výpočtu DPH: {method}")
        vat_rows.append(VatRow(rate_percent=rate, base=base, vat=vat))

    total_base = sum((row.base for row in vat_rows), ZERO)
    total_vat = sum((row.vat for row in vat_rows), ZERO)
    total_before = total_base + total_vat
    if rounding_mode == "whole_czk":
        rounded = total_before.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    elif rounding_mode == "none":
        rounded = total_before
    else:
        raise ValueError(f"Neznámý režim zaokrouhlení: {rounding_mode}")
    return Totals(
        lines=results,
        vat_rows=vat_rows,
        total_base=total_base,
        total_vat=total_vat,
        total_before_rounding=total_before,
        rounding=round2(rounded - total_before),
        total=round2(rounded),
    )
