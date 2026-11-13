from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import actor_from_request
from audit import services as audit
from audit.models import AuditEvent
from core.concurrency import ConcurrentUpdate, lock_and_check
from core.decorators import profile_view

from . import services
from .forms import (
    BankAccountForm,
    LogoForm,
    NumberSeriesForm,
    ProfileForm,
    ReminderRuleForm,
    ShiftNumberForm,
    VatRateForm,
    VatRegimeForm,
)
from .models import BankAccount, BillingProfile, NumberSeries, ReminderRule, VatRate, VatRegime

KINDS = {
    "ucty": (BankAccount, BankAccountForm, "Bankovní účet"),
    "sazby": (VatRate, VatRateForm, "Sazba DPH"),
    "rezimy": (VatRegime, VatRegimeForm, "Režim DPH"),
    "rady": (NumberSeries, NumberSeriesForm, "Číselná řada"),
    "upominky": (ReminderRule, ReminderRuleForm, "Upomínkové pravidlo"),
}


@profile_view(admin=True)
def settings_page(request, profile):
    series = list(profile.series.all())
    for s in series:
        s.used = services.series_used(s)
        s.counters_list = list(s.counters.order_by("-period"))
        s.example = s.preview(timezone.localdate().year)
    return render(request, "profiles/settings.html", {
        "profile": profile,
        "accounts": profile.bank_accounts.all(),
        "rates": profile.vat_rates.all(),
        "regimes": profile.vat_regimes.all(),
        "series": series,
        "rules": profile.reminder_rules.all(),
        "events": AuditEvent.objects.filter(profile=profile, action__startswith=("profile"))[:20],
    })


@profile_view(admin=True)
def profile_edit(request, profile):
    if request.method == "POST":
        try:
            with transaction.atomic():
                locked = lock_and_check(BillingProfile.objects, profile.pk, request.POST.get("version"))
                before = audit.snapshot(locked)
                form = ProfileForm(request.POST, instance=locked)
                if form.is_valid():
                    obj = form.save(commit=False)
                    obj.version += 1
                    obj.save()
                    b, a = audit.diff(before, audit.snapshot(obj))
                    audit.record(actor_from_request(request), "profile.update", profile=obj, obj=obj,
                                 before=b, after=a)
                    for warning in services.duplicate_warnings(obj):
                        messages.warning(request, warning)
                    messages.success(request, "Nastavení profilu uloženo. Vystavené doklady se nemění.")
                    return redirect("profiles:settings", slug=obj.slug)
        except ConcurrentUpdate as exc:
            messages.error(request, exc.message)
            return redirect(request.path)
    else:
        form = ProfileForm(instance=profile)
    return render(request, "profiles/profile_form.html", {"form": form, "profile": profile})


@profile_view(admin=True)
def item_edit(request, profile, kind, pk=None):
    if kind not in KINDS:
        raise Http404
    model, form_class, title = KINDS[kind]
    instance = get_object_or_404(model, pk=pk, profile=profile) if pk else model(profile=profile)
    extra = {}
    if model is NumberSeries and pk:
        extra["used"] = services.series_used(instance)
    actor = actor_from_request(request)
    if request.method == "POST":
        try:
            with transaction.atomic():
                if pk and hasattr(instance, "version"):
                    instance = lock_and_check(model.objects.filter(profile=profile), pk,
                                              request.POST.get("version"))
                before = audit.snapshot(instance) if pk else None
                form = form_class(request.POST, instance=instance, **extra)
                if form.is_valid():
                    obj = form.save(commit=False)
                    obj.profile = profile
                    if pk and hasattr(obj, "version"):
                        obj.version += 1
                    obj.save()
                    after = audit.snapshot(obj)
                    b, a = audit.diff(before, after) if pk else (None, after)
                    audit.record(actor, f"{model._meta.model_name}.{'update' if pk else 'create'}",
                                 profile=profile, obj=obj, before=b, after=a)
                    messages.success(request, f"{title} uložen(a).")
                    return redirect("profiles:settings", slug=profile.slug)
        except ConcurrentUpdate as exc:
            messages.error(request, exc.message)
            return redirect(request.path)
        except IntegrityError:
            form.add_error(None, "Záznam se stejnou hodnotou už v profilu existuje.")
    else:
        form = form_class(instance=instance, **extra)
    return render(request, "profiles/item_form.html", {
        "form": form, "profile": profile, "title": title, "kind": kind, "item": instance,
    })


@profile_view(admin=True)
def item_delete(request, profile, kind, pk):
    if kind != "upominky" or request.method != "POST":
        raise Http404
    rule = get_object_or_404(ReminderRule, pk=pk, profile=profile)
    audit.record(actor_from_request(request), "reminderrule.delete", profile=profile, obj=rule,
                 before=audit.snapshot(rule))
    rule.delete()
    messages.success(request, "Pravidlo odstraněno.")
    return redirect("profiles:settings", slug=profile.slug)


@profile_view(admin=True)
def series_shift(request, profile, pk):
    series = get_object_or_404(NumberSeries, pk=pk, profile=profile)
    form = ShiftNumberForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.shift_next_number(
                actor_from_request(request), series, str(form.cleaned_data["year"]),
                form.cleaned_data["next_value"], form.cleaned_data["reason"],
            )
            messages.success(request, "Další číslo řady bylo posunuto.")
            return redirect("profiles:settings", slug=profile.slug)
        except ValidationError as exc:
            form.add_error(None, exc.messages)
    return render(request, "profiles/shift_form.html", {"form": form, "profile": profile, "series": series})


@profile_view(admin=True)
def logo_upload(request, profile):
    form = LogoForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        before = profile.logo.name if profile.logo else ""
        if profile.logo:
            profile.logo.delete(save=False)
        profile.logo = form.cleaned_data["logo"]
        profile.version += 1
        profile.save()
        audit.record(actor_from_request(request), "profile.logo", profile=profile, obj=profile,
                     before={"logo": before}, after={"logo": profile.logo.name})
        messages.success(request, "Logo uloženo. Projeví se na nově vystavených dokladech.")
        return redirect("profiles:settings", slug=profile.slug)
    return render(request, "profiles/logo_form.html", {"form": form, "profile": profile})


@profile_view()
def logo_file(request, profile):
    if not profile.logo:
        raise Http404
    return FileResponse(profile.logo.open("rb"))
