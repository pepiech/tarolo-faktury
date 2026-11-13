"""Sestavení dat dokladu pro šablonu PDF/náhledu ze snímků (dict) – stejná cesta pro
náhled i vystavení, aby náhled věrně odpovídal výslednému PDF."""

import base64
import hashlib
import json
import mimetypes
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from core.choices import PaymentMethod

from . import calc
from .spayd import QrPaymentUnavailable, build_spayd, qr_png_data_uri

PREVIEW_NUMBER = "NÁVRH"
PREVIEW_WATERMARK = "NÁHLED – NENÍ VYSTAVENÝ DOKLAD"


def issuer_snapshot(profile):
    return {
        "profile_id": profile.pk,
        "name": profile.name,
        "legal_form": profile.legal_form,
        "street": profile.street,
        "city": profile.city,
        "zip_code": profile.zip_code,
        "country": profile.country,
        "ico": profile.ico,
        "dic": profile.dic,
        "vat_payer": profile.vat_payer,
        "register_note": profile.register_note,
        "email": profile.email,
        "phone": profile.phone,
        "web": profile.web,
        "logo": profile.logo.name if profile.logo else "",
        "intro_text": profile.invoice_intro_text,
        "footer_text": profile.invoice_footer_text,
    }


def partner_snapshot(partner):
    return {
        "partner_id": partner.pk,
        "name": partner.name,
        "ico": partner.ico,
        "dic": partner.dic,
        "street": partner.street,
        "city": partner.city,
        "zip_code": partner.zip_code,
        "country": partner.country,
        "billing_email": partner.billing_email,
    }


def bank_snapshot(account):
    if account is None:
        return {}
    return {
        "bank_account_id": account.pk,
        "label": account.label,
        "account_number": account.account_number,
        "iban": account.iban,
        "bic": account.bic,
        "bank_name": account.bank_name,
    }


def regime_snapshot(regime):
    return {
        "vat_regime_id": regime.pk,
        "code": regime.code,
        "label": regime.label,
        "applies_vat": regime.applies_vat,
        "invoice_text": regime.invoice_text,
    }


def _logo_data_uri(relative_path):
    if not relative_path:
        return ""
    path = Path(settings.MEDIA_ROOT) / relative_path
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def line_inputs(lines):
    return [
        calc.LineInput(
            quantity=line["quantity"],
            unit_price=line["unit_price"],
            discount_percent=line.get("discount_percent") or calc.ZERO,
            vat_rate_percent=line.get("vat_rate_percent") or calc.ZERO,
        )
        for line in lines
    ]


def compute(lines, *, applies_vat, method, rounding_mode):
    return calc.calculate(
        line_inputs(lines), applies_vat=applies_vat, method=method, rounding_mode=rounding_mode
    )


def build(
    *,
    issuer,
    partner,
    bank,
    regime,
    header,
    lines,
    totals,
    number=None,
    preview=False,
    issued_by=None,
):
    """header: dict s daty, symboly, formou úhrady a texty. lines: list dictů položek."""
    payment_method = header["payment_method"]
    doc = {
        "preview": preview,
        "watermark": PREVIEW_WATERMARK if preview else "",
        "test_watermark": settings.APP_ENVIRONMENT != "production",
        "title": "FAKTURA – DAŇOVÝ DOKLAD" if issuer.get("vat_payer") else "FAKTURA",
        "number": number or PREVIEW_NUMBER,
        "variable_symbol": header.get("variable_symbol", ""),
        "constant_symbol": header.get("constant_symbol", ""),
        "specific_symbol": header.get("specific_symbol", ""),
        "issuer": issuer,
        "logo_data_uri": _logo_data_uri(issuer.get("logo")),
        "partner": partner,
        "bank": bank,
        "regime": regime,
        "issue_date": header["issue_date"],
        "taxable_supply_date": header["taxable_supply_date"],
        "due_date": header["due_date"],
        "payment_method": payment_method,
        "payment_method_label": PaymentMethod(payment_method).label,
        "order_reference": header.get("order_reference", ""),
        "public_note": header.get("public_note", ""),
        "lines": [
            {
                "description": line["description"],
                "quantity": Decimal(str(line["quantity"])),
                "unit": line.get("unit", ""),
                "unit_price": Decimal(str(line["unit_price"])),
                "discount_percent": Decimal(str(line.get("discount_percent") or 0)),
                "vat_rate_percent": result.vat_rate_percent,
                "base": result.base,
                "vat": result.vat,
                "total": result.total,
            }
            for line, result in zip(lines, totals.lines, strict=True)
        ],
        "vat_rows": totals.vat_rows,
        "total_base": totals.total_base,
        "total_vat": totals.total_vat,
        "total_before_rounding": totals.total_before_rounding,
        "rounding": totals.rounding,
        "total": totals.total,
        "issued_by": issued_by or {},
    }
    doc["qr"] = qr_for(doc)
    return doc


def qr_for(doc):
    """QR Platba ze stejných hodnot, které jsou čitelně na dokladu."""
    if doc["payment_method"] != PaymentMethod.BANK_TRANSFER:
        return None
    try:
        payload = build_spayd(
            iban=doc["bank"].get("iban", ""),
            bic=doc["bank"].get("bic", ""),
            amount=doc["total"],
            variable_symbol=doc["variable_symbol"],
            due_date=doc["due_date"],
            message="" if doc["preview"] else f"Faktura {doc['number']}",
        )
    except QrPaymentUnavailable as exc:
        return {"error": str(exc)}
    return {"payload": payload, "data_uri": qr_png_data_uri(payload)}


def data_hash(doc):
    """Hash kanonických dat dokladu (bez obrázků), ze kterých vzniklo PDF."""

    def default(value):
        if isinstance(value, calc.VatRow):
            return value.as_dict()
        return str(value)

    payload = {k: v for k, v in doc.items() if k not in {"logo_data_uri", "qr"}}
    payload["qr_payload"] = (doc.get("qr") or {}).get("payload", "")
    text = json.dumps(payload, default=default, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()
