"""QR Platba (SPAYD 1.0) – https://qr-platba.cz/pro-vyvojare/specifikace-formatu/"""

import re
from decimal import Decimal

import segno

from profiles.validators import iban_is_valid, normalize_iban


class QrPaymentUnavailable(Exception):
    """QR kód nelze vytvořit; zpráva je srozumitelná pro uživatele."""


def _clean(value, max_len):
    # Hvězdička je oddělovač polí – v hodnotách se nahrazuje; ořez na povolenou délku.
    text = re.sub(r"\s+", " ", str(value)).replace("*", " ").strip()
    return text[:max_len]


def build_spayd(*, iban, bic="", amount, variable_symbol="", due_date=None, message=""):
    iban = normalize_iban(iban)
    if not iban or not iban_is_valid(iban):
        raise QrPaymentUnavailable("Chybí platný IBAN bankovního účtu.")
    amount = Decimal(amount)
    if amount <= 0:
        raise QrPaymentUnavailable("Částka k úhradě musí být kladná.")
    if amount > Decimal("9999999.99"):
        raise QrPaymentUnavailable("Částka přesahuje limit QR Platby.")
    acc = iban + (f"+{bic.replace(' ', '').upper()}" if bic else "")
    parts = ["SPD", "1.0", f"ACC:{acc}", f"AM:{amount:.2f}", "CC:CZK"]
    if due_date:
        parts.append(f"DT:{due_date:%Y%m%d}")
    if variable_symbol:
        if not re.fullmatch(r"\d{1,10}", variable_symbol):
            raise QrPaymentUnavailable("Variabilní symbol musí mít 1–10 číslic.")
        parts.append(f"X-VS:{variable_symbol}")
    if message:
        parts.append(f"MSG:{_clean(message, 60)}")
    return "*".join(parts)


def qr_png_data_uri(payload):
    qr = segno.make(payload, error="m", micro=False)
    return qr.png_data_uri(scale=6, border=2)


def parse_spayd(payload):
    """Rozloží SPAYD na slovník (pro testy a kontrolu)."""
    parts = payload.split("*")
    if parts[:2] != ["SPD", "1.0"]:
        raise ValueError("Není SPAYD 1.0")
    result = {}
    for part in parts[2:]:
        key, _, value = part.partition(":")
        result[key] = value
    return result
