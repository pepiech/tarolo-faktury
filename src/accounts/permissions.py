"""Oprávnění po fakturačních profilech. Kontroluje se ve službách i ve views."""

from dataclasses import dataclass

INVOICES_VIEW = "invoices:view"
INVOICES_MODIFY = "invoices:modify"
PARTNERS_VIEW = "partners:view"
PARTNERS_MODIFY = "partners:modify"
ALL_PERMS = {INVOICES_VIEW, INVOICES_MODIFY, PARTNERS_VIEW, PARTNERS_MODIFY}


class AccessDenied(Exception):
    """Uživatel nemá oprávnění. Webové rozhraní odpovídá 404, aby neprozradilo existenci dat."""


@dataclass(frozen=True)
class Actor:
    user: object
    interface: str = "web"
    request_id: str = ""


def actor_from_request(request):
    return Actor(user=request.user, interface="web", request_id=getattr(request, "request_id", ""))


def _membership(user, profile):
    cache = getattr(user, "_membership_cache", None)
    if cache is None:
        cache = {}
        user._membership_cache = cache
    if profile.pk not in cache:
        cache[profile.pk] = user.memberships.filter(profile=profile).first()
    return cache[profile.pk]


def has_perm(user, profile, perm):
    if perm not in ALL_PERMS:
        raise ValueError(perm)
    if user is None or not getattr(user, "is_active", False):
        return False
    if user.is_admin:
        return True
    m = _membership(user, profile)
    if m is None:
        return False
    if perm == INVOICES_VIEW:
        return m.can_view_invoices or m.can_modify_invoices
    if perm == INVOICES_MODIFY:
        return m.can_modify_invoices
    if perm == PARTNERS_VIEW:
        return m.has_any
    return m.can_modify_partners


def require(user, profile, perm):
    if not has_perm(user, profile, perm):
        raise AccessDenied(perm)


def require_admin(user):
    if user is None or not user.is_active or not user.is_admin:
        raise AccessDenied("admin")


def accessible_profiles(user, include_inactive=True):
    """Profily, ke kterým má uživatel jakékoli oprávnění (správce: všechny)."""
    from django.db.models import Q

    from profiles.models import BillingProfile

    qs = BillingProfile.objects.all()
    if not include_inactive:
        qs = qs.filter(is_active=True)
    if user.is_admin:
        return qs
    # Jedno volání filter(), aby se podmínky vztahovaly ke stejnému členství.
    return qs.filter(
        Q(memberships__user=user)
        & (
            Q(memberships__can_view_invoices=True)
            | Q(memberships__can_modify_invoices=True)
            | Q(memberships__can_modify_partners=True)
        )
    ).distinct()
