import csv
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import INVOICES_MODIFY, INVOICES_VIEW, actor_from_request
from audit.models import AuditEvent
from core.concurrency import ConcurrentUpdate
from core.decorators import profile_view
from partners.models import Partner

from . import document, pdf, services
from .forms import (
    CancelForm,
    DeliveryForm,
    InvoiceFilterForm,
    InvoiceForm,
    PaymentForm,
    ReminderForm,
    line_formset,
)
from .models import Invoice, Payment


def _invoice(profile, pk):
    return get_object_or_404(
        Invoice.objects.for_profile(profile).with_payment_state().select_related(
            "partner", "series", "bank_account", "vat_regime", "issued_by", "cancelled_by"),
        pk=pk,
    )


def _error_messages(exc):
    if hasattr(exc, "message_dict"):
        return [m for msgs in exc.message_dict.values() for m in msgs]
    return exc.messages


# --- seznam ------------------------------------------------------------------

@profile_view(INVOICES_VIEW)
def invoice_list(request, profile):
    qs = Invoice.objects.for_profile(profile).with_payment_state().select_related("partner")
    filters = InvoiceFilterForm(request.GET or None, profile=profile)
    qs = filters.apply(qs)
    if request.GET.get("format") == "csv":
        return _csv(profile, qs)
    return render(request, "invoicing/list.html", {"invoices": qs[:500], "filters": filters,
                                                   "count": qs.count()})


def _csv(profile, qs):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="faktury-{profile.slug}-{timezone.localdate():%Y%m%d}.csv"'
    )
    response.write("﻿")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(["Vystavitel", "IČO vystavitele", "Číslo", "Stav", "Odběratel", "IČO odběratele",
                     "Vystaveno", "DUZP", "Splatnost", "Základ", "DPH", "Celkem", "Uhrazeno", "Zbývá",
                     "Stav úhrady", "Po splatnosti", "Předání", "VS"])

    def num(value):
        return f"{value:.2f}".replace(".", ",")

    for inv in qs:
        writer.writerow([
            profile.name, profile.ico, inv.number or "", inv.get_status_display(), inv.partner.name,
            inv.partner.ico, inv.issue_date.strftime("%d.%m.%Y"),
            inv.taxable_supply_date.strftime("%d.%m.%Y"), inv.due_date.strftime("%d.%m.%Y"),
            num(inv.total_base), num(inv.total_vat), num(inv.total), num(inv.paid_amount),
            num(inv.remaining_amount), _payment_label(inv), "ano" if inv.is_overdue else "ne",
            inv.get_delivery_status_display(), inv.variable_symbol,
        ])
    return response


def _payment_label(inv):
    from .models import PaymentState

    if inv.status == "draft":
        return ""
    return PaymentState(inv.payment_state).label


# --- návrh -------------------------------------------------------------------

def _bound_forms(request, profile, initial=None, lines_initial=None):
    if request.method == "POST":
        form = InvoiceForm(request.POST, profile=profile, initial=initial)
        formset = line_formset(profile, data=request.POST)
    else:
        form = InvoiceForm(profile=profile, initial=initial)
        formset = line_formset(profile, initial=lines_initial)
    return form, formset


def _collect(form, formset):
    if not (form.is_valid() and formset.is_valid()):
        return None, None
    lines = [f.line() for f in formset.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
    if not lines:
        formset._non_form_errors = formset.error_class(["Faktura musí mít alespoň jednu položku."])
        return None, None
    return form.header(), lines


@profile_view(INVOICES_MODIFY)
def invoice_create(request, profile):
    partner = None
    if request.GET.get("partner"):
        partner = Partner.objects.filter(profile=profile, pk=request.GET["partner"]).first()
    initial = InvoiceForm.defaults(profile, partner)
    default_rate = profile.vat_rates.filter(is_active=True).first()
    lines_initial = [{"quantity": 1, "discount_percent": 0,
                      "vat_rate_percent": str(default_rate.rate_percent) if default_rate else "0.00"}]
    form, formset = _bound_forms(request, profile, initial, lines_initial)
    if request.method == "POST":
        header, lines = _collect(form, formset)
        if header is not None:
            try:
                invoice = services.create_draft(actor_from_request(request), profile, header, lines)
                messages.success(request, "Návrh faktury uložen. Číslo se přidělí až při vystavení.")
                return redirect("invoicing:detail", slug=profile.slug, pk=invoice.pk)
            except ValidationError as exc:
                for msg in _error_messages(exc):
                    form.add_error(None, msg)
    return render(request, "invoicing/form.html", {"form": form, "formset": formset, "invoice": None})


@profile_view(INVOICES_MODIFY)
def invoice_edit(request, profile, pk):
    invoice = _invoice(profile, pk)
    if not invoice.is_draft:
        messages.error(request, "Vystavenou fakturu nelze upravovat.")
        return redirect("invoicing:detail", slug=profile.slug, pk=pk)
    initial = {**services.header_of(invoice), "version": invoice.version}
    lines_initial = [{**line, "vat_rate_percent": str(line["vat_rate_percent"])}
                     for line in services.lines_of(invoice)]
    form, formset = _bound_forms(request, profile, initial, lines_initial)
    if request.method == "POST":
        header, lines = _collect(form, formset)
        if header is not None:
            try:
                services.update_draft(actor_from_request(request), invoice,
                                      form.cleaned_data.get("version"), header, lines)
                messages.success(request, "Návrh uložen.")
                return redirect("invoicing:detail", slug=profile.slug, pk=pk)
            except ConcurrentUpdate as exc:
                form.add_error(None, exc.message)
            except ValidationError as exc:
                for msg in _error_messages(exc):
                    form.add_error(None, msg)
    return render(request, "invoicing/form.html", {"form": form, "formset": formset, "invoice": invoice})


@profile_view(INVOICES_MODIFY)
@require_POST
def invoice_delete(request, profile, pk):
    invoice = _invoice(profile, pk)
    try:
        services.delete_draft(actor_from_request(request), invoice, request.POST.get("version"))
        messages.success(request, "Návrh smazán.")
        return redirect("invoicing:list", slug=profile.slug)
    except (ValidationError, ConcurrentUpdate) as exc:
        messages.error(request, getattr(exc, "message", None) or "; ".join(_error_messages(exc)))
        return redirect("invoicing:detail", slug=profile.slug, pk=pk)


@profile_view(INVOICES_MODIFY)
@require_POST
def totals(request, profile):
    """htmx: průběžné součty z neuloženého formuláře (stejný výpočet jako PDF)."""
    form, formset = _bound_forms(request, profile)
    header, lines = _collect(form, formset)
    if header is None:
        return render(request, "invoicing/_totals.html", {"invalid": True})
    regime = header["vat_regime"]
    result = document.compute(lines, applies_vat=regime.applies_vat and profile.vat_payer,
                              method=profile.vat_calc_method, rounding_mode=profile.rounding_mode)
    return render(request, "invoicing/_totals.html", {"t": result, "applies_vat": regime.applies_vat})


# --- náhled ------------------------------------------------------------------

def _pdf_response(content, filename, inline=True):
    response = HttpResponse(content, content_type="application/pdf")
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response


@profile_view(INVOICES_MODIFY)
@require_POST
def preview_unsaved(request, profile):
    form, formset = _bound_forms(request, profile)
    header, lines = _collect(form, formset)
    if header is None:
        return render(request, "invoicing/form.html", {
            "form": form, "formset": formset, "invoice": None,
            "preview_error": "Náhled nelze vytvořit – opravte chyby ve formuláři.",
        }, status=400)
    doc = services.build_preview(profile, header, lines)
    return _pdf_response(pdf.render_pdf(doc), "nahled.pdf")


@profile_view(INVOICES_VIEW)
def preview_draft(request, profile, pk):
    invoice = _invoice(profile, pk)
    if not invoice.is_draft:
        return redirect("invoicing:pdf", slug=profile.slug, pk=pk)
    doc = services.build_preview(profile, services.header_of(invoice), services.lines_of(invoice))
    return _pdf_response(pdf.render_pdf(doc), f"nahled-{invoice.pk}.pdf")


# --- detail a vystavení ------------------------------------------------------

@profile_view(INVOICES_VIEW)
def invoice_detail(request, profile, pk):
    invoice = _invoice(profile, pk)
    ctx = {
        "invoice": invoice,
        "lines": invoice.lines.all(),
        "payments": invoice.payments.select_related("created_by", "reverses"),
        "deliveries": invoice.deliveries.select_related("created_by"),
        "reminders": invoice.reminders.select_related("created_by"),
        "events": AuditEvent.objects.filter(profile=profile).filter(
            object_id=str(invoice.pk), object_type="invoicing.invoice"
        ).select_related("actor")[:50],
        "payment_form": PaymentForm(initial={"amount": invoice.remaining_amount if invoice.remaining_amount > 0 else None,
                                             "method": invoice.payment_method}),
        "delivery_form": DeliveryForm(),
        "reminder_form": ReminderForm(),
        "cancel_form": CancelForm(),
    }
    if invoice.is_draft:
        ctx["issue_problems"] = services.issue_problems(invoice)
    return render(request, "invoicing/detail.html", ctx)


@profile_view(INVOICES_MODIFY)
def issue_confirm(request, profile, pk):
    invoice = _invoice(profile, pk)
    if not invoice.is_draft:
        return redirect("invoicing:detail", slug=profile.slug, pk=pk)
    if request.method == "POST":
        try:
            issued = services.issue(actor_from_request(request), invoice, request.POST.get("version"))
            messages.success(request, f"Faktura {issued.number} byla vystavena.")
            return redirect("invoicing:detail", slug=profile.slug, pk=pk)
        except ConcurrentUpdate as exc:
            messages.error(request, exc.message)
        except ValidationError as exc:
            for msg in _error_messages(exc):
                messages.error(request, msg)
        return redirect("invoicing:issue", slug=profile.slug, pk=pk)
    return render(request, "invoicing/issue_confirm.html", {
        "invoice": invoice,
        "expected_number": services.peek_next_number(invoice.series, invoice.issue_date),
        "problems": services.issue_problems(invoice),
    })


@profile_view(INVOICES_VIEW)
def invoice_pdf(request, profile, pk):
    invoice = _invoice(profile, pk)
    if invoice.is_draft or not invoice.pdf_file:
        raise Http404
    path = Path(settings.MEDIA_ROOT) / invoice.pdf_file
    if not path.is_file():
        raise Http404
    return FileResponse(path.open("rb"), content_type="application/pdf",
                        filename=f"faktura-{invoice.number}.pdf",
                        as_attachment=request.GET.get("stahnout") == "1")


# --- akce nad vystavenou fakturou -----------------------------------------------

def _post_action(request, profile, pk, form_class, action, success):
    invoice = _invoice(profile, pk)
    form = form_class(request.POST)
    if form.is_valid():
        try:
            action(invoice, form.cleaned_data)
            messages.success(request, success)
        except ConcurrentUpdate as exc:
            messages.error(request, exc.message)
        except ValidationError as exc:
            for msg in _error_messages(exc):
                messages.error(request, msg)
    else:
        for errors in form.errors.values():
            for msg in errors:
                messages.error(request, msg)
    return redirect("invoicing:detail", slug=profile.slug, pk=pk)


@profile_view(INVOICES_MODIFY)
@require_POST
def cancel(request, profile, pk):
    actor = actor_from_request(request)
    return _post_action(
        request, profile, pk, CancelForm,
        lambda inv, d: services.cancel(actor, inv, request.POST.get("version"), d["reason"]),
        "Faktura stornována.",
    )


@profile_view(INVOICES_MODIFY)
@require_POST
def payment_add(request, profile, pk):
    actor = actor_from_request(request)
    return _post_action(request, profile, pk, PaymentForm,
                        lambda inv, d: services.add_payment(actor, inv, **d), "Platba zapsána.")


@profile_view(INVOICES_MODIFY)
@require_POST
def payment_reverse(request, profile, pk, payment_pk):
    invoice = _invoice(profile, pk)
    payment = get_object_or_404(Payment, pk=payment_pk, invoice=invoice)
    try:
        services.reverse_payment(actor_from_request(request), payment, request.POST.get("note", ""))
        messages.success(request, "Platba zrušena protioperací.")
    except ValidationError as exc:
        for msg in _error_messages(exc):
            messages.error(request, msg)
    return redirect("invoicing:detail", slug=profile.slug, pk=pk)


@profile_view(INVOICES_MODIFY)
@require_POST
def delivered(request, profile, pk):
    actor = actor_from_request(request)
    return _post_action(request, profile, pk, DeliveryForm,
                        lambda inv, d: services.mark_delivered(actor, inv, **d),
                        "Předání zaznamenáno.")


@profile_view(INVOICES_MODIFY)
@require_POST
def reminder(request, profile, pk):
    actor = actor_from_request(request)
    return _post_action(request, profile, pk, ReminderForm,
                        lambda inv, d: services.record_reminder(actor, inv, **d),
                        "Upomínka zaznamenána.")
