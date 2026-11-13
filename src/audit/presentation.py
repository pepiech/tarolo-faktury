"""Čitelná podoba auditních záznamů pro web (souhrn změny, popisky, odkazy na objekty)."""

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from core.templatetags.cz import money

ACTION_LABELS = {
    "invoice.draft_create": "Založen návrh faktury",
    "invoice.draft_update": "Upraven návrh faktury",
    "invoice.draft_delete": "Smazán návrh faktury",
    "invoice.issue": "Vystavena faktura",
    "invoice.cancel": "Stornována faktura",
    "invoice.delivered": "Faktura předána",
    "invoice.reminder": "Zaznamenána upomínka",
    "payment.create": "Zapsána platba",
    "payment.reverse": "Zrušena platba",
    "partner.create": "Založen partner",
    "partner.update": "Upraven partner",
    "partner.copy": "Partner zkopírován",
    "profile.create": "Založen profil",
    "profile.update": "Upraveno nastavení profilu",
    "profile.logo": "Změněno logo profilu",
    "profile.bootstrap": "Profil založen při instalaci",
    "profile.default_account": "Doplněn výchozí účet",
    "series.shift": "Posunuto číslování řady",
    "user.create": "Založen uživatel",
    "user.update": "Upraven uživatel",
    "user.bootstrap_admin": "Založen správce při instalaci",
    "membership.change": "Změněna oprávnění",
    "mcp.call": "Volání MCP nástroje",
    "reminderrule.delete": "Smazáno pravidlo upomínek",
}
GENERIC_VERBS = {"create": "Založeno", "update": "Upraveno", "delete": "Smazáno"}
ACTION_GROUPS = {
    "invoice.": "Faktury", "payment.": "Platby", "partner.": "Partneři", "profile.": "Profily",
    "user.": "Uživatelé", "membership.": "Oprávnění", "mcp.": "MCP",
}
OBJECT_LABELS = {
    "invoicing.invoice": "Faktura", "invoicing.payment": "Platba", "invoicing.reminderrecord": "Upomínka",
    "invoicing.deliveryrecord": "Předání", "partners.partner": "Partner", "profiles.billingprofile": "Profil",
    "profiles.bankaccount": "Bankovní účet", "profiles.vatrate": "Sazba DPH", "profiles.vatregime": "Režim DPH",
    "profiles.numberseries": "Číselná řada", "profiles.reminderrule": "Pravidlo upomínek", "accounts.user": "Uživatel",
}
FIELD_LABELS = {
    "status": "stav", "number": "číslo", "total": "celkem", "total_base": "základ", "total_vat": "DPH",
    "rounding": "zaokrouhlení", "amount": "částka", "paid_on": "datum platby", "method": "způsob",
    "reference": "reference", "note": "poznámka", "name": "název", "email": "e-mail", "is_admin": "správce",
    "is_active": "aktivní", "ico": "IČO", "dic": "DIČ", "street": "ulice", "city": "obec", "psc": "PSČ",
    "country": "stát", "partner_id": "odběratel", "series_id": "číselná řada", "bank_account_id": "bankovní účet",
    "vat_regime_id": "režim DPH", "pdf_sha256": "otisk PDF", "delivered_on": "datum předání",
    "next": "další číslo", "period": "období", "logo": "logo", "default_bank_account": "výchozí účet",
    "default_bank_account_id": "výchozí účet", "tool": "nástroj", "ok": "úspěch", "error": "chyba",
    "can_view_invoices": "vidí faktury", "can_modify_invoices": "upravuje faktury",
    "can_modify_partners": "upravuje partnery", "delivery_status": "předání", "reverses_id": "ruší platbu",
    "reminded_on": "datum upomínky", "result_note": "výsledek", "cancel_reason": "důvod storna",
    "vat_summary": "rekapitulace DPH", "source": "zdroj", "id": "ID", "invoice_id": "faktura", "created_at": "vytvořeno",
    "updated_at": "upraveno", "created_by_id": "vytvořil", "updated_by_id": "upravil", "version": "verze",
    "profile_id": "profil", "issued_at": "vystaveno", "issued_by_id": "vystavil", "cancelled_at": "stornováno",
    "cancelled_by_id": "stornoval", "last_login_at": "poslední přihlášení",
}
# Technická pole, která v souhrnu nezobrazujeme (v detailu zůstávají).
NOISE_FIELDS = {
    "id", "version", "created_at", "updated_at", "created_by_id", "updated_by_id", "profile_id",
    "document_type", "issuer_snapshot", "partner_snapshot", "bank_snapshot", "vat_regime_snapshot",
    "vat_summary", "data_hash", "pdf_file", "issued_at", "issued_by_id", "invoice_id", "origin",
}
KEY_FIELDS = {
    "invoicing.invoice": ["number", "partner_id", "issue_date", "due_date", "total"],
    "invoicing.payment": ["amount", "paid_on", "method", "reference"],
    "partners.partner": ["name", "ico", "city"],
    "accounts.user": ["email", "name", "is_admin"],
    "profiles.billingprofile": ["name", "ico"],
}
MONEY_FIELDS = {"total", "total_base", "total_vat", "rounding", "amount", "unit_price"}
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
MAX_SUMMARY_ITEMS = 4


def action_label(action):
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    obj, _, verb = action.partition(".")
    return f"{GENERIC_VERBS.get(verb, verb or action)} ({obj})"


def action_choices(existing):
    """Volby pro filtr akcí: skupiny (prefix) a konkrétní akce, které se v auditu vyskytují."""
    known = sorted(set(existing) | set(ACTION_LABELS), key=lambda a: (a.split(".")[0], action_label(a)))
    groups = []
    for prefix, title in ACTION_GROUPS.items():
        items = [(a, action_label(a)) for a in known if a.startswith(prefix)]
        if items:
            groups.append((title, [(prefix, f"{title} – vše")] + items))
    rest = [(a, action_label(a)) for a in known if not any(a.startswith(p) for p in ACTION_GROUPS)]
    if rest:
        groups.append(("Ostatní", rest))
    return groups


def _model(object_type):
    try:
        return apps.get_model(object_type) if object_type else None
    except (LookupError, ValueError):
        return None


def _field(model, key):
    if model is None:
        return None
    for f in model._meta.concrete_fields:
        if f.attname == key or f.name == key:
            return f
    try:
        return model._meta.get_field(key)
    except FieldDoesNotExist:
        return None


def field_label(model, key):
    if key in FIELD_LABELS:
        return FIELD_LABELS[key]
    f = _field(model, key)
    if f is not None and str(f.verbose_name) != f.name.replace("_", " "):
        return str(f.verbose_name)
    return key.removesuffix("_id").replace("_", " ")


def format_value(model, key, value, refs=None):
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "ano" if value else "ne"
    f = _field(model, key)
    if f is not None and getattr(f, "choices", None):
        display = dict(f.flatchoices).get(value)
        if display is not None:
            return str(display)
    if key in MONEY_FIELDS:
        try:
            return money(value)
        except (ValueError, ArithmeticError, TypeError):
            pass
    if isinstance(value, str):
        if ISO_DATE.match(value):
            return date.fromisoformat(value).strftime("%-d. %-m. %Y")
        if ISO_DATETIME.match(value):
            try:
                dt = datetime.fromisoformat(value)
                if timezone.is_aware(dt):
                    dt = timezone.localtime(dt)
                return dt.strftime("%-d. %-m. %Y %H:%M")
            except ValueError:
                pass
        return value if len(value) <= 60 else value[:59] + "…"
    if isinstance(value, list):
        n = len(value)
        return f"{n} {'položka' if n == 1 else 'položky' if 2 <= n <= 4 else 'položek'}"
    if isinstance(value, dict):
        return f"{len(value)} údajů"
    if f is not None and f.is_relation and f.related_model is not None and refs:
        name = refs.get((f.related_model._meta.label_lower, value))
        if name:
            return name
    if key.endswith("_id") or (f is not None and f.is_relation):
        return f"#{value}"
    return str(value)


@dataclass
class Change:
    label: str
    before: str = ""
    after: str = ""
    kind: str = "set"  # set | change | remove


@dataclass
class EventView:
    event: object
    action_label: str
    object_label: str = ""
    object_url: str = ""
    summary: list = field(default_factory=list)
    summary_text: str = ""
    rows: list = field(default_factory=list)
    raw: str = ""


def _ordered(model, keys):
    """Pořadí polí podle modelu (JSONB pořadí klíčů nezachovává); technická pole na konec."""
    order = {}
    if model is not None:
        for i, f in enumerate(model._meta.concrete_fields):
            order.setdefault(f.attname, i)
            order.setdefault(f.name, i)
    return sorted(keys, key=lambda k: (k in NOISE_FIELDS, order.get(k, len(order)), k))


def _changes(model, before, after, only_keys=None, refs=None, skip_empty=False):
    before = before if isinstance(before, dict) else None
    after = after if isinstance(after, dict) else None
    keys = _ordered(model, list(dict.fromkeys(list((after or {}).keys()) + list((before or {}).keys()))))
    if only_keys is not None:
        keys = [k for k in only_keys if k in keys]
    out = []
    for key in keys:
        label = field_label(model, key)
        if before is not None and after is not None:
            if before.get(key) == after.get(key):
                continue
            out.append(Change(label, format_value(model, key, before.get(key), refs),
                              format_value(model, key, after.get(key), refs), "change"))
        elif after is not None:
            if skip_empty and after.get(key) in (None, "", [], {}):
                continue
            out.append(Change(label, "", format_value(model, key, after.get(key), refs), "set"))
        else:
            if skip_empty and before.get(key) in (None, "", [], {}):
                continue
            out.append(Change(label, format_value(model, key, before.get(key), refs), "", "remove"))
    return out


def _summary(event, model, refs):
    before, after = event.before, event.after
    if event.action == "mcp.call" and isinstance(after, dict):
        items = [Change("nástroj", after=str(after.get("tool", "")))]
        items.append(Change("výsledek", after="OK" if after.get("ok") else f"chyba: {after.get('error') or '?'}"))
        args = _mcp_args(event.note)
        if args:
            items.append(Change("parametry", after=args))
        return items
    if isinstance(before, dict) and isinstance(after, dict):
        keys = [k for k in _ordered(model, list(dict.fromkeys(list(after) + list(before)))) if k not in NOISE_FIELDS]
        return _changes(model, before, after, keys, refs)
    data = after if isinstance(after, dict) else before
    if not isinstance(data, dict):
        return []
    preferred = [k for k in KEY_FIELDS.get(event.object_type, []) if data.get(k) not in (None, "")]
    keys = preferred or [k for k in _ordered(model, list(data))
                         if k not in NOISE_FIELDS and data.get(k) not in (None, "", [], {})]
    return _changes(model, before if after is None else None, after, keys[:MAX_SUMMARY_ITEMS], refs)


def _mcp_args(note):
    try:
        args = json.loads(note)
    except (TypeError, ValueError):
        return ""
    if not isinstance(args, dict):
        return ""
    parts = [f"{k}={json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v}"
             for k, v in args.items() if k not in ("client_request_id",)]
    text = ", ".join(parts)
    return text if len(text) <= 120 else text[:119] + "…"


def _summary_text(items):
    parts = []
    for c in items:
        if c.kind == "change":
            parts.append(f"{c.label}: {c.before} → {c.after}")
        elif c.kind == "remove":
            parts.append(f"{c.label}: {c.before}")
        else:
            parts.append(f"{c.label}: {c.after}")
    return " · ".join(parts)


def _raw(event):
    data = {"before": event.before, "after": event.after}
    if event.note:
        data["note"] = event.note
    if event.request_id:
        data["request_id"] = event.request_id
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _resolve_objects(events):
    """Hromadně dohledá popisky a odkazy objektů (faktura č., partner, platba → faktura)."""
    ids = {}
    for e in events:
        if e.object_id.isdigit():
            ids.setdefault(e.object_type, set()).add(int(e.object_id))
    invoice_of = {}
    from invoicing.models import Invoice, Payment, ReminderRecord
    from partners.models import Partner
    for model, key in ((Payment, "invoicing.payment"), (ReminderRecord, "invoicing.reminderrecord")):
        if ids.get(key):
            for pk, inv in model.objects.filter(pk__in=ids[key]).values_list("pk", "invoice_id"):
                invoice_of[(key, pk)] = inv
    invoice_ids = set(ids.get("invoicing.invoice", set())) | set(invoice_of.values())
    invoices = {i.pk: i for i in Invoice.objects.filter(pk__in=invoice_ids).select_related("profile")}
    partners = {p.pk: p for p in Partner.objects.filter(pk__in=ids.get("partners.partner", set())).select_related("profile")}
    return invoices, partners, invoice_of


def _resolve_refs(events):
    """Hromadně převede cizí klíče v před/po (odběratel, účet, uživatel…) na čitelné názvy."""
    wanted = {}
    for e in events:
        model = _model(e.object_type)
        for data in (e.before, e.after):
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                f = _field(model, key)
                if f is not None and f.is_relation and f.related_model is not None and isinstance(value, int):
                    wanted.setdefault(f.related_model, set()).add(value)
    refs = {}
    for related, pks in wanted.items():
        label = related._meta.label_lower
        for obj in related.objects.filter(pk__in=pks):
            if label == "invoicing.invoice":
                text = f"faktura {_invoice_text(obj)}"
            elif label == "accounts.user":
                text = obj.email
            else:
                text = str(obj)
            refs[(label, obj.pk)] = text if len(text) <= 60 else text[:59] + "…"
    return refs


def _invoice_text(inv):
    return f"č. {inv.number}" if inv.number else f"návrh #{inv.pk}"


def _url(name, **kwargs):
    try:
        return reverse(name, kwargs=kwargs)
    except NoReverseMatch:
        return ""


def present(events):
    events = list(events)
    invoices, partners, invoice_of = _resolve_objects(events)
    refs = _resolve_refs(events)
    views = []
    for e in events:
        model = _model(e.object_type)
        base = OBJECT_LABELS.get(e.object_type, e.object_type.split(".")[-1] if e.object_type else "")
        label, url = (f"{base} #{e.object_id}" if e.object_id else base), ""
        pk = int(e.object_id) if e.object_id.isdigit() else None
        if e.object_type == "invoicing.invoice" and pk in invoices:
            inv = invoices[pk]
            label = f"Faktura {_invoice_text(inv)}"
            url = _url("invoicing:detail", slug=inv.profile.slug, pk=inv.pk)
        elif (e.object_type, pk) in invoice_of and invoice_of[(e.object_type, pk)] in invoices:
            inv = invoices[invoice_of[(e.object_type, pk)]]
            label = f"{base} #{pk} · faktura {_invoice_text(inv)}"
            url = _url("invoicing:detail", slug=inv.profile.slug, pk=inv.pk)
        elif e.object_type == "partners.partner" and pk in partners:
            p = partners[pk]
            label = f"Partner {p.name}"
            url = _url("partners:detail", slug=p.profile.slug, pk=p.pk)
        summary = _summary(e, model, refs)
        text = _summary_text(summary)
        if e.note and e.action != "mcp.call":
            text = f"{text} · „{e.note}“" if text else f"„{e.note}“"
        views.append(EventView(
            event=e, action_label=action_label(e.action), object_label=label, object_url=url,
            summary=summary, summary_text=text or "—",
            rows=_changes(model, e.before, e.after, refs=refs,
                          skip_empty=not (isinstance(e.before, dict) and isinstance(e.after, dict))),
            raw=_raw(e),
        ))
    return views
