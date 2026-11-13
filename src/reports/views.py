from django.shortcuts import render
from django.utils import timezone

from accounts.permissions import INVOICES_VIEW, accessible_profiles, has_perm
from core.decorators import admin_view, profile_view
from invoicing import services as invoicing_services
from invoicing.models import Invoice

from . import services


def _year(request):
    raw = request.GET.get("rok", "")
    if raw == "vse":
        return None
    try:
        return int(raw)
    except ValueError:
        return timezone.localdate().year


@profile_view()
def dashboard(request, profile):
    can_view = has_perm(request.user, profile, INVOICES_VIEW)
    year = _year(request)
    ctx = {"profile": profile, "can_view": can_view, "year": year,
           "years": range(timezone.localdate().year, timezone.localdate().year - 6, -1)}
    if can_view:
        ctx["summary"] = services.summary(profile, year)
        ctx["reminders"] = invoicing_services.reminders_due(profile)
        ctx["overdue"] = (Invoice.objects.for_profile(profile).with_payment_state()
                          .filter(is_overdue=True).select_related("partner").order_by("due_date")[:20])
        ctx["recent"] = (Invoice.objects.for_profile(profile).with_payment_state()
                         .select_related("partner")[:10])
    return render(request, "reports/dashboard.html", ctx)


@admin_view
def global_overview(request):
    year = _year(request)
    rows = [{"profile": p, "s": services.summary(p, year)} for p in accessible_profiles(request.user)]
    return render(request, "reports/global.html", {
        "rows": rows, "year": year,
        "years": range(timezone.localdate().year, timezone.localdate().year - 6, -1),
    })
