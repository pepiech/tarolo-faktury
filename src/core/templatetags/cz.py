"""České formátování částek a dat (dostupné ve všech šablonách)."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template
from django.utils import timezone

register = template.Library()

NBSP = " "


def format_amount(value, places=2):
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    quant = Decimal(1).scaleb(-places)
    number = number.quantize(quant, rounding=ROUND_HALF_UP)
    sign = "-" if number < 0 else ""
    integer, _, fraction = f"{abs(number):.{places}f}".partition(".")
    groups = []
    while integer:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    result = NBSP.join(groups)
    if places:
        result += "," + fraction
    return sign + result


@register.filter
def money(value):
    """1936.5 -> '1 936,50'"""
    return format_amount(value, 2)


@register.filter
def czk(value):
    """1936.5 -> '1 936,50 Kč'"""
    formatted = format_amount(value, 2)
    return f"{formatted}{NBSP}Kč" if formatted else ""


@register.filter
def qty(value):
    """Množství bez zbytečných nul: 1.000 -> '1', 2.500 -> '2,5'."""
    if value is None or value == "":
        return ""
    number = Decimal(str(value)).normalize()
    text = format_amount(number, max(0, -number.as_tuple().exponent))
    return text


@register.filter
def percent(value):
    if value is None or value == "":
        return ""
    number = Decimal(str(value)).normalize()
    return format_amount(number, max(0, -number.as_tuple().exponent))


@register.filter
def cz_date(value):
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = timezone.localtime(value) if timezone.is_aware(value) else value
        return value.strftime("%d.%m.%Y %H:%M")
    return value.strftime("%d.%m.%Y")
