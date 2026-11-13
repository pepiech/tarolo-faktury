from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce

from invoicing.models import Invoice, InvoiceStatus

ZERO = Decimal("0.00")


def summary(profile, year=None, today=None):
    """Souhrny vždy jen za jeden profil (nikdy se nesčítají napříč profily)."""
    qs = Invoice.objects.for_profile(profile).filter(status=InvoiceStatus.ISSUED)
    if year:
        qs = qs.filter(issue_date__year=year)
    qs = qs.with_payment_state(today)
    agg = qs.aggregate(
        count=Count("id"),
        issued=Coalesce(Sum("total"), ZERO),
        paid=Coalesce(Sum("paid_amount"), ZERO),
        remaining=Coalesce(Sum("remaining_amount", filter=Q(remaining_amount__gt=0)), ZERO),
        overdue=Coalesce(Sum("remaining_amount", filter=Q(is_overdue=True)), ZERO),
        overdue_count=Count("id", filter=Q(is_overdue=True)),
    )
    drafts = Invoice.objects.for_profile(profile).filter(status=InvoiceStatus.DRAFT).count()
    return {**agg, "drafts": drafts}
