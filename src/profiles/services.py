from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.permissions import require_admin
from audit import services as audit

from . import numbering
from .models import BillingProfile, NumberCounter, NumberSeries, ReminderRule, VatRate, VatRegime

DEFAULT_VAT_RATES = [("Základní", Decimal("21")), ("Snížená", Decimal("12")), ("Nulová", Decimal("0"))]
DEFAULT_REGIMES = [
    ("tuzemsko", "Tuzemské plnění", True, ""),
    ("prenesena", "Přenesená daňová povinnost", False,
     "Daň odvede zákazník (přenesení daňové povinnosti podle § 92a zákona o DPH)."),
    ("osvobozeno", "Osvobozené plnění", False, "Plnění osvobozené od DPH."),
]
NON_PAYER_REGIME = ("neplatce", "Neplátce DPH", False, "Dodavatel není plátcem DPH.")


def seed_defaults(profile):
    """Výchozí sazby, režimy DPH, číselná řada {YY}{N} a upomínková pravidla nového profilu."""
    if profile.vat_payer:
        for order, (label, rate) in enumerate(DEFAULT_VAT_RATES):
            VatRate.objects.get_or_create(
                profile=profile, rate_percent=rate, defaults={"label": label, "order": order}
            )
        regimes = DEFAULT_REGIMES
    else:
        VatRate.objects.get_or_create(
            profile=profile, rate_percent=Decimal("0"), defaults={"label": "Bez DPH"}
        )
        regimes = [NON_PAYER_REGIME]
    created_regimes = []
    for code, label, applies_vat, text in regimes:
        regime, _ = VatRegime.objects.get_or_create(
            profile=profile, code=code,
            defaults={"label": label, "applies_vat": applies_vat, "invoice_text": text},
        )
        created_regimes.append(regime)
    series = profile.series.first() or NumberSeries.objects.create(
        profile=profile, name="Faktury", format="{YY}{N}", start_value=1, min_width=0
    )
    for days, label in [(7, "1. upomínka"), (21, "2. upomínka")]:
        ReminderRule.objects.get_or_create(
            profile=profile, days_after_due=days, defaults={"label": label}
        )
    changed = False
    if profile.default_vat_regime is None:
        profile.default_vat_regime = created_regimes[0]
        changed = True
    if profile.default_series is None:
        profile.default_series = series
        changed = True
    if changed:
        profile.save(update_fields=["default_vat_regime", "default_series", "updated_at"])


@transaction.atomic
def create_profile(actor, profile):
    require_admin(actor.user)
    profile.save()
    seed_defaults(profile)
    audit.record(actor, "profile.create", profile=profile, obj=profile, after=audit.snapshot(profile))
    return profile


def duplicate_warnings(profile):
    """Varování na shodné DIČ u jiného profilu (IČO je unikátní constraintem)."""
    warnings = []
    if profile.dic:
        clash = BillingProfile.objects.filter(dic__iexact=profile.dic).exclude(pk=profile.pk)
        for other in clash:
            warnings.append(f"Stejné DIČ {profile.dic} má i profil „{other.name}“.")
    return warnings


def series_used(series):
    return NumberCounter.objects.filter(series=series, last_value__isnull=False).exists()


@transaction.atomic
def shift_next_number(actor, series, period, next_value, reason):
    """Posun řady na vyšší, dosud nepoužitou hodnotu (jen správce, s důvodem)."""
    require_admin(actor.user)
    if not reason.strip():
        raise ValidationError("Uveďte důvod změny.")
    series = NumberSeries.objects.select_for_update().get(pk=series.pk)
    period = period if numbering.is_yearly(series.format) else ""
    counter, _ = NumberCounter.objects.select_for_update().get_or_create(
        series=series, period=period, defaults={"last_value": None}
    )
    current_next = (counter.last_value + 1) if counter.last_value is not None else series.start_value
    if next_value <= current_next - 1 or next_value < current_next:
        raise ValidationError(
            f"Další číslo lze jen zvýšit (aktuálně by následovalo pořadí {current_next})."
        )
    before = {"period": period, "next": current_next}
    counter.last_value = next_value - 1
    counter.save()
    audit.record(
        actor, "series.shift", profile=series.profile, obj=series,
        before=before, after={"period": period, "next": next_value}, note=reason,
    )
