"""Společné filtry, období a souhrny pro čtecí MCP nástroje (počty a součty vždy po profilech)."""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from invoicing.models import Invoice, InvoiceStatus, Payment

from . import errors as E
from .errors import ToolError

ZERO = Decimal("0.00")

PERIODS = [
    "today", "yesterday", "this_week", "last_week", "last_7_days", "this_month", "last_month",
    "last_30_days", "this_quarter", "last_quarter", "this_year", "last_year",
]
PERIOD_SCHEMA = {
    "type": "string",
    "enum": PERIODS,
    "description": "Pojmenované období počítané na serveru (Europe/Prague, týden od pondělí): today, "
                   "yesterday, this_week, last_week, last_7_days, this_month, last_month, last_30_days, "
                   "this_quarter, last_quarter, this_year, last_year. Přednost mají výslovná data.",
}
DATE_FIELDS = {"issue_date": "datum vystavení", "due_date": "datum splatnosti", "paid_on": "datum platby"}


def today():
    return timezone.localdate()


def _quarter_start(d):
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _month_start(d):
    return d.replace(day=1)


def resolve_period(name, ref=None):
    """Vrátí (od, do) včetně, pro pojmenované období."""
    d = ref or today()
    if name == "today":
        return d, d
    if name == "yesterday":
        y = d - timedelta(days=1)
        return y, y
    if name == "this_week":
        start = d - timedelta(days=d.weekday())
        return start, start + timedelta(days=6)
    if name == "last_week":
        start = d - timedelta(days=d.weekday() + 7)
        return start, start + timedelta(days=6)
    if name == "last_7_days":
        return d - timedelta(days=6), d
    if name == "last_30_days":
        return d - timedelta(days=29), d
    if name == "this_month":
        start = _month_start(d)
        return start, (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    if name == "last_month":
        end = _month_start(d) - timedelta(days=1)
        return _month_start(end), end
    if name == "this_quarter":
        start = _quarter_start(d)
        return start, (_quarter_start(start + timedelta(days=95))) - timedelta(days=1)
    if name == "last_quarter":
        end = _quarter_start(d) - timedelta(days=1)
        return _quarter_start(end), end
    if name == "this_year":
        return date(d.year, 1, 1), date(d.year, 12, 31)
    if name == "last_year":
        return date(d.year - 1, 1, 1), date(d.year - 1, 12, 31)
    raise ToolError(E.INVALID_ARGUMENTS, f"Neznámé období: {name}")


def parse_date(value, name):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(E.INVALID_ARGUMENTS, f"Neplatné datum v poli {name}: {value}") from exc


def date_range(args, field, from_key, to_key):
    """Rozsah pro jedno datové pole: výslovná data mají přednost před pojmenovaným obdobím."""
    start = parse_date(args.get(from_key), from_key)
    end = parse_date(args.get(to_key), to_key)
    if args.get("period") and args.get("period_field", "issue_date") == field and not (start or end):
        start, end = resolve_period(args["period"])
    if start and end and start > end:
        raise ToolError(E.INVALID_ARGUMENTS, f"{from_key} je později než {to_key}.")
    return start, end


def applied_filters(args):
    """Popis skutečně použitých filtrů (včetně přepočtu období) pro výstup."""
    out = {k: v for k, v in args.items() if k not in {"limit", "cursor", "compact", "client_request_id"}}
    if args.get("period"):
        start, end = resolve_period(args["period"])
        out["period_resolved"] = {"field": args.get("period_field", "issue_date"),
                                  "from": start.isoformat(), "to": end.isoformat()}
    return out


def payments_in_range(start, end):
    """Faktury s aspoň jednou (nestornovanou) platbou s datem úhrady v rozsahu."""
    cond = Q(invoice=OuterRef("pk"), amount__gt=0, reverses__isnull=True, reversal__isnull=True)
    if start:
        cond &= Q(paid_on__gte=start)
    if end:
        cond &= Q(paid_on__lte=end)
    return Exists(Payment.objects.filter(cond))


def filter_invoices(qs, args):
    """Filtry společné pro search_invoices a invoice_stats."""
    if args.get("query"):
        q = args["query"].strip()
        qs = qs.filter(Q(number__icontains=q) | Q(variable_symbol=q) | Q(partner__name__icontains=q))
    if args.get("partner_id"):
        qs = qs.filter(partner_id=args["partner_id"])
    if args.get("partner_ico"):
        qs = qs.filter(partner__ico=args["partner_ico"])
    status = args.get("status")
    if status:
        qs = qs.filter(status=status)
    if args.get("payment_state"):
        qs = qs.filter(payment_state=args["payment_state"]).exclude(status=InvoiceStatus.DRAFT)
    if args.get("overdue_only"):
        qs = qs.filter(is_overdue=True)
    for field, lookup in (("issue_date", "issue_date"), ("due_date", "due_date")):
        prefix = "issued" if field == "issue_date" else "due"
        start, end = date_range(args, field, f"{prefix}_from", f"{prefix}_to")
        if start:
            qs = qs.filter(**{f"{lookup}__gte": start})
        if end:
            qs = qs.filter(**{f"{lookup}__lte": end})
    start, end = date_range(args, "paid_on", "paid_from", "paid_to")
    if start or end:
        qs = qs.filter(payments_in_range(start, end))
    return qs


def totals(qs):
    """Souhrn jednoho profilu: počty podle stavu; částky jen z vystavených (ne návrhů a stornovaných)."""
    issued = Q(status=InvoiceStatus.ISSUED)
    agg = qs.aggregate(
        count=Count("id"),
        drafts=Count("id", filter=Q(status=InvoiceStatus.DRAFT)),
        issued_count=Count("id", filter=issued),
        cancelled=Count("id", filter=Q(status=InvoiceStatus.CANCELLED)),
        issued_total=Coalesce(Sum("total", filter=issued), ZERO),
        paid=Coalesce(Sum("paid_amount", filter=issued), ZERO),
        remaining=Coalesce(Sum("remaining_amount", filter=issued & Q(remaining_amount__gt=0)), ZERO),
        overdue_count=Count("id", filter=Q(is_overdue=True)),
        overdue_amount=Coalesce(Sum("remaining_amount", filter=Q(is_overdue=True)), ZERO),
    )
    money_keys = ("issued_total", "paid", "remaining", "overdue_amount")
    return {k: (f"{v:.2f}" if k in money_keys else v) for k, v in agg.items()}


def invoice_base(profiles):
    return (Invoice.objects.filter(profile__in=profiles).with_payment_state()
            .select_related("profile", "partner").prefetch_related("payments__created_by"))


SORTS = {
    "issue_date_desc": ("-issue_date", "-pk"),
    "issue_date_asc": ("issue_date", "pk"),
    "due_date_asc": ("due_date", "pk"),
    "total_desc": ("-total", "-pk"),
    "remaining_desc": ("-remaining_amount", "-pk"),
}
