from django.db import transaction
from django.db.models import Q

from accounts.permissions import PARTNERS_MODIFY, require, require_admin
from audit import services as audit

from .models import Partner

COPY_FIELDS = [
    "name", "ico", "dic", "vat_payer", "street", "city", "zip_code", "country", "billing_email",
    "contact_person", "phone", "is_customer", "is_supplier",
]


def duplicates(profile, partner):
    """Podobní partneři ve stejném profilu – jen varování, ne zamítnutí."""
    cond = Q()
    if partner.ico:
        cond |= Q(ico=partner.ico)
    if partner.dic:
        cond |= Q(dic__iexact=partner.dic)
    if partner.name and partner.street:
        cond |= Q(name__iexact=partner.name, street__iexact=partner.street)
    if not cond:
        return Partner.objects.none()
    qs = Partner.objects.filter(profile=profile).filter(cond)
    if partner.pk:
        qs = qs.exclude(pk=partner.pk)
    return qs


@transaction.atomic
def save_partner(actor, profile, partner, before=None):
    require(actor.user, profile, PARTNERS_MODIFY)
    created = partner.pk is None
    partner.profile = profile
    if not created:
        partner.version += 1
    partner.save()
    after = audit.snapshot(partner)
    if created:
        audit.record(actor, "partner.create", profile=profile, obj=partner, after=after)
    else:
        b, a = audit.diff(before, after)
        audit.record(actor, "partner.update", profile=profile, obj=partner, before=b, after=a)
    return partner


@transaction.atomic
def copy_to_profile(actor, partner, target_profile):
    require_admin(actor.user)
    copy = Partner(profile=target_profile, **{f: getattr(partner, f) for f in COPY_FIELDS})
    copy.save()
    audit.record(actor, "partner.copy", profile=target_profile, obj=copy,
                 after=audit.snapshot(copy), note=f"Zkopírováno z partnera #{partner.pk}")
    return copy
