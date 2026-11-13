"""Formáty číselných řad: {N} (průběžné číslo) a {YY}/{YYYY} (rok data vystavení)."""

import re

from django.core.exceptions import ValidationError

TOKEN_RE = re.compile(r"\{([A-Z]+)\}")
KNOWN_TOKENS = {"N", "YY", "YYYY"}
LITERAL_RE = re.compile(r"^[A-Za-z0-9/_.\-]*$")


def validate_format(fmt):
    tokens = TOKEN_RE.findall(fmt or "")
    unknown = set(tokens) - KNOWN_TOKENS
    if unknown:
        raise ValidationError(f"Neznámé položky formátu: {', '.join(sorted(unknown))}.")
    if tokens.count("N") != 1:
        raise ValidationError("Formát musí obsahovat právě jednou {N}.")
    literal = TOKEN_RE.sub("", fmt)
    if not LITERAL_RE.match(literal):
        raise ValidationError("Pevný text smí obsahovat jen písmena, číslice a znaky / _ . -")
    if len(render(fmt, 10**9, 2099, 0)) > 32:
        raise ValidationError("Výsledné číslo by bylo příliš dlouhé.")


def is_yearly(fmt):
    return "{YY}" in fmt or "{YYYY}" in fmt


def period_for(fmt, year):
    """Období čítače: rok u ročních řad, jinak prázdné (průběžná řada)."""
    return str(year) if is_yearly(fmt) else ""


def render(fmt, n, year, min_width):
    sequence = str(n).zfill(min_width or 0)
    return (
        fmt.replace("{YYYY}", f"{year:04d}")
        .replace("{YY}", f"{year % 100:02d}")
        .replace("{N}", sequence)
    )


def digits_only(number):
    return re.sub(r"\D", "", number or "")
