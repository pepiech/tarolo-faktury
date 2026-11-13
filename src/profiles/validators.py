import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

IBAN_LENGTHS = {"CZ": 24, "SK": 24, "DE": 22, "AT": 20, "PL": 28, "HU": 28}


def normalize_iban(value):
    return re.sub(r"\s+", "", value or "").upper()


def iban_is_valid(value):
    iban = normalize_iban(value)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    expected = IBAN_LENGTHS.get(iban[:2])
    if expected and len(iban) != expected:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def validate_iban(value):
    if not iban_is_valid(value):
        raise ValidationError("Neplatný IBAN (kontrolní číslice nebo délka nesouhlasí).")


def validate_bic(value):
    bic = (value or "").replace(" ", "").upper()
    if bic and not re.fullmatch(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?", bic):
        raise ValidationError("Neplatný BIC/SWIFT kód.")


def validate_ico(value):
    if value and not re.fullmatch(r"\d{1,8}", value):
        raise ValidationError("IČO se skládá z nejvýše 8 číslic.")


validate_vs = RegexValidator(r"^\d{1,10}$", "Variabilní symbol smí obsahovat jen číslice (nejvýše 10).")
validate_ss = RegexValidator(r"^\d{1,10}$", "Specifický symbol smí obsahovat jen číslice (nejvýše 10).")
validate_ks = RegexValidator(r"^\d{1,4}$", "Konstantní symbol smí obsahovat jen číslice (nejvýše 4).")
