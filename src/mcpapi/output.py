"""Převod dat do výstupu MCP nástrojů. Každý výsledek nese vystavující profil."""

from django.urls import reverse

from invoicing.models import InvoiceStatus, PaymentState


def money(value):
    return f"{value:.2f}" if value is not None else None


def profile_ref(profile):
    return {"id": profile.pk, "slug": profile.slug, "name": profile.name, "ico": profile.ico}


def partner_out(partner, detail=False):
    data = {
        "id": partner.pk,
        "profile": profile_ref(partner.profile),
        "name": partner.name,
        "ico": partner.ico,
        "dic": partner.dic,
        "city": partner.city,
        "is_active": partner.is_active,
    }
    if detail:
        data.update({
            "street": partner.street, "zip_code": partner.zip_code, "country": partner.country,
            "vat_payer": partner.vat_payer, "billing_email": partner.billing_email,
            "contact_person": partner.contact_person, "phone": partner.phone,
            "due_days_override": partner.due_days_override,
            "payment_method_override": partner.payment_method_override or None,
            "version": partner.version,
        })
    return data


def _abs(request, url):
    return request.build_absolute_uri(url) if request is not None else url


def invoice_out(invoice, request=None, detail=False):
    """invoice musí být anotovaná with_payment_state()."""
    profile = invoice.profile
    kw = {"slug": profile.slug, "pk": invoice.pk}
    is_draft = invoice.status == InvoiceStatus.DRAFT
    last = None
    payments = list(invoice.payments.all()) if not is_draft else []
    if payments:
        p = max(payments, key=lambda x: (x.created_at, x.pk))
        last = {
            "paid_on": p.paid_on.isoformat(), "amount": money(p.amount), "method": p.method,
            "source": p.source, "recorded_at": p.created_at.isoformat(),
            "recorded_by": p.created_by.email, "is_reversal": p.reverses_id is not None,
        }
    data = {
        "id": invoice.pk,
        "profile": profile_ref(profile),
        "document_type": invoice.document_type,
        "status": invoice.status,
        "is_draft": is_draft,
        "number": invoice.number,
        "display_number": invoice.display_number,
        "origin": "issued_in_app" if not is_draft else "draft",
        "partner": {
            "id": invoice.partner_id,
            "name": invoice.partner_snapshot.get("name") if not is_draft else invoice.partner.name,
            "ico": invoice.partner_snapshot.get("ico") if not is_draft else invoice.partner.ico,
        },
        "issue_date": invoice.issue_date.isoformat(),
        "taxable_supply_date": invoice.taxable_supply_date.isoformat(),
        "due_date": invoice.due_date.isoformat(),
        "currency": "CZK",
        "total_base": money(invoice.total_base),
        "total_vat": money(invoice.total_vat),
        "total": money(invoice.total),
        "variable_symbol": invoice.variable_symbol or None,
        "version": invoice.version,
        "web_url": _abs(request, reverse("invoicing:detail", kwargs=kw)),
    }
    if is_draft:
        data["preview_url"] = _abs(request, reverse("invoicing:preview_draft", kwargs=kw))
    else:
        paid = getattr(invoice, "paid_amount", None)
        data.update({
            "paid": money(paid),
            "remaining": money(getattr(invoice, "remaining_amount", None)),
            "payment_state": invoice.payment_state,
            "payment_state_label": PaymentState(invoice.payment_state).label,
            "overdue": bool(getattr(invoice, "is_overdue", False)),
            "last_payment": last,
            "delivery_status": invoice.delivery_status,
            "issued_at": invoice.issued_at.isoformat() if invoice.issued_at else None,
            "pdf_url": _abs(request, reverse("invoicing:pdf", kwargs=kw)),
        })
        if invoice.status == InvoiceStatus.CANCELLED:
            data["cancel_reason"] = invoice.cancel_reason
    if detail:
        data["lines"] = [
            {
                "description": line.description, "quantity": str(line.quantity.normalize()),
                "unit": line.unit, "unit_price": money(line.unit_price),
                "discount_percent": str(line.discount_percent.normalize()),
                "vat_rate_percent": str(line.vat_rate_percent.normalize()),
                "base": money(line.line_base), "vat": money(line.line_vat), "total": money(line.line_total),
            }
            for line in invoice.lines.all()
        ]
        data["vat_summary"] = invoice.vat_summary
        data["payment_method"] = invoice.payment_method
        data["public_note"] = invoice.public_note
        if not is_draft:
            data["payments"] = [
                {"id": p.pk, "paid_on": p.paid_on.isoformat(), "amount": money(p.amount), "method": p.method,
                 "reference": p.reference, "note": p.note, "reverses": p.reverses_id,
                 "recorded_at": p.created_at.isoformat(), "recorded_by": p.created_by.email}
                for p in payments
            ]
    return data
