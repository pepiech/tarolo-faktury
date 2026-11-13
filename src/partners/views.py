import csv

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import PARTNERS_MODIFY, PARTNERS_VIEW, actor_from_request
from audit import services as audit
from core.concurrency import ConcurrentUpdate, lock_and_check
from core.decorators import profile_view
from invoicing.models import Invoice

from . import services
from .forms import CopyPartnerForm, PartnerForm
from .models import Partner


def _filtered(profile, params):
    qs = Partner.objects.filter(profile=profile)
    q = params.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(ico__startswith=q) | Q(dic__icontains=q) | Q(city__icontains=q))
    if params.get("neaktivni") != "1":
        qs = qs.filter(is_active=True)
    return qs


@profile_view(PARTNERS_VIEW)
def partner_list(request, profile):
    qs = _filtered(profile, request.GET).annotate(invoice_count=Count("invoices"))
    if request.GET.get("format") == "csv":
        return _csv(profile, qs)
    return render(request, "partners/list.html", {"partners": qs, "q": request.GET.get("q", ""),
                                                  "show_inactive": request.GET.get("neaktivni") == "1"})


def _csv(profile, qs):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="partneri-{profile.slug}.csv"'
    response.write("﻿")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(["Název", "IČO", "DIČ", "Plátce DPH", "Ulice", "Obec", "PSČ", "Stát", "E-mail",
                     "Kontakt", "Telefon", "Odběratel", "Dodavatel", "Aktivní"])
    for p in qs:
        writer.writerow([p.name, p.ico, p.dic, "ano" if p.vat_payer else "ne", p.street, p.city,
                         p.zip_code, p.country, p.billing_email, p.contact_person, p.phone,
                         "ano" if p.is_customer else "ne", "ano" if p.is_supplier else "ne",
                         "ano" if p.is_active else "ne"])
    return response


@profile_view(PARTNERS_VIEW)
def partner_detail(request, profile, pk):
    partner = get_object_or_404(Partner, pk=pk, profile=profile)
    invoices = Invoice.objects.for_profile(profile).filter(partner=partner).with_payment_state()[:50]
    copy_form = CopyPartnerForm(exclude_profile=profile) if request.user.is_admin else None
    return render(request, "partners/detail.html", {"partner": partner, "invoices": invoices,
                                                    "copy_form": copy_form})


@profile_view(PARTNERS_MODIFY)
def partner_edit(request, profile, pk=None):
    partner = get_object_or_404(Partner, pk=pk, profile=profile) if pk else Partner(profile=profile)
    if request.method == "POST":
        try:
            with transaction.atomic():
                if pk:
                    partner = lock_and_check(Partner.objects.filter(profile=profile), pk,
                                             request.POST.get("version"))
                before = audit.snapshot(partner) if pk else None
                form = PartnerForm(request.POST, instance=partner)
                if form.is_valid():
                    obj = form.save(commit=False)
                    dupes = services.duplicates(profile, obj)
                    saved = services.save_partner(actor_from_request(request), profile, obj, before)
                    for d in dupes:
                        messages.warning(request, f"Podobný partner už existuje: {d.name}"
                                                  f"{' (IČO ' + d.ico + ')' if d.ico else ''}.")
                    messages.success(request, "Partner uložen.")
                    return redirect("partners:detail", slug=profile.slug, pk=saved.pk)
        except ConcurrentUpdate as exc:
            messages.error(request, exc.message)
            return redirect(request.path)
    else:
        form = PartnerForm(instance=partner)
    return render(request, "partners/form.html", {"form": form, "partner": partner})


@profile_view(admin=True)
def partner_copy(request, profile, pk):
    partner = get_object_or_404(Partner, pk=pk, profile=profile)
    form = CopyPartnerForm(request.POST, exclude_profile=profile)
    if request.method == "POST" and form.is_valid():
        target = form.cleaned_data["target"]
        copy = services.copy_to_profile(actor_from_request(request), partner, target)
        messages.success(request, f"Partner zkopírován do profilu {target.name}.")
        return redirect("partners:detail", slug=target.slug, pk=copy.pk)
    return redirect("partners:detail", slug=profile.slug, pk=pk)
