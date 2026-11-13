"""Dohledání ekonomického subjektu v ARES (veřejné REST API Ministerstva financí, bez klíče).

Data z ARES slouží jen k předvyplnění – partner se uloží až po potvrzení uživatelem
(web) nebo výslovným voláním nástroje create_partner (MCP)."""

import json
import logging
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

BASE_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty"
TIMEOUT = 8


class AresError(Exception):
    """ARES nedostupný nebo nevrátil použitelná data (zpráva je pro uživatele)."""


def normalize_ico(value):
    digits = re.sub(r"\D", "", value or "")
    if not digits or len(digits) > 8:
        return ""
    return digits.zfill(8)


def ico_checksum_ok(ico):
    if not re.fullmatch(r"\d{8}", ico or ""):
        return False
    total = sum(int(ico[i]) * (8 - i) for i in range(7))
    check = (11 - total % 11) % 10
    return check == int(ico[7])


def _request(url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method="POST" if data else "GET",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:  # noqa: S310 - pevná https adresa
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        logger.warning("ARES HTTP %s pro %s", exc.code, url)
        raise AresError(f"ARES odpověděl chybou {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.warning("ARES nedostupný: %s", exc)
        raise AresError("ARES je momentálně nedostupný, zkuste to později.") from exc


def _format_psc(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[:3]} {digits[3:]}" if len(digits) == 5 else digits


def _street(sidlo):
    street = sidlo.get("nazevUlice") or sidlo.get("nazevCastiObce") or sidlo.get("nazevObce") or ""
    number = str(sidlo.get("cisloDomovni") or "")
    orient = str(sidlo.get("cisloOrientacni") or "")
    if orient:
        orient += sidlo.get("cisloOrientacniPismeno") or ""
        number = f"{number}/{orient}" if number else orient
    return f"{street} {number}".strip()


def _city(sidlo):
    city = sidlo.get("nazevObce") or ""
    part = sidlo.get("nazevCastiObce") or ""
    if part and part != city and sidlo.get("nazevUlice"):
        return f"{city} - {part}"
    return city


def to_partner_data(subject):
    """Převede záznam ARES na pole partnera (bez uložení)."""
    sidlo = subject.get("sidlo") or {}
    dic = subject.get("dic") or ""
    return {
        "name": subject.get("obchodniJmeno") or "",
        "ico": normalize_ico(subject.get("ico") or ""),
        "dic": dic,
        "vat_payer": bool(dic),
        "street": _street(sidlo),
        "city": _city(sidlo),
        "zip_code": _format_psc(sidlo.get("psc")),
        "country": sidlo.get("nazevStatu") or "Česká republika",
        "address_text": sidlo.get("textovaAdresa") or "",
        "dissolved_on": subject.get("datumZaniku") or "",
    }


def lookup(ico):
    """Subjekt podle IČO, nebo None, pokud v ARES neexistuje."""
    ico = normalize_ico(ico)
    if not ico_checksum_ok(ico):
        raise AresError("Neplatné IČO (nesouhlasí kontrolní číslice).")
    subject = _request(f"{BASE_URL}/{ico}")
    return to_partner_data(subject) if subject else None


def search(name, limit=10):
    name = (name or "").strip()
    if len(name) < 3:
        raise AresError("Pro hledání v ARES zadejte alespoň 3 znaky názvu.")
    data = _request(f"{BASE_URL}/vyhledat", {"obchodniJmeno": name, "pocet": min(limit, 20), "start": 0})
    subjects = (data or {}).get("ekonomickeSubjekty") or []
    return {
        "total": (data or {}).get("pocetCelkem", len(subjects)),
        "results": [to_partner_data(s) for s in subjects],
    }
