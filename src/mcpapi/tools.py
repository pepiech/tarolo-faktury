"""MCP nástroje fakturační aplikace (schéma verze viz SCHEMA_VERSION).

Pravidla (zadání kap. 5): každé volání je autentizované (Cloudflare Access), autorizované vůči
konkrétnímu profilu a auditované; zápisy vyžadují výslovný profil a client_request_id;
nejednoznačný dotaz vrací kandidáty místo svévolného výběru; vystavení a storno jsou
samostatné operace s potvrzením."""

import base64
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from accounts.permissions import (
    INVOICES_MODIFY,
    INVOICES_VIEW,
    PARTNERS_MODIFY,
    PARTNERS_VIEW,
    accessible_profiles,
    has_perm,
)
from core.choices import PaymentMethod
from invoicing import pdf
from invoicing import services as inv
from invoicing.models import Invoice, InvoiceStatus
from partners import ares
from partners import services as partner_services
from partners.models import Partner
from profiles.models import BankAccount, NumberSeries, VatRegime, VsRule
from reports import services as reports

from . import errors as E
from .errors import ToolError
from .output import invoice_out, money, partner_out, profile_ref

SCHEMA_VERSION = "1.0.0"
MAX_LIMIT = 50


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    handler: object
    write: bool = False
    annotations: dict = field(default_factory=dict)


TOOLS = {}


def tool(name, *, title, description, properties, required=(), write=False, read_only=None,
         destructive=False, idempotent=False):
    schema = {
        "type": "object",
        "properties": dict(properties),
        # Chybějící profil neřeší schéma, ale resolve_profile → chyba profile_required se seznamem profilů.
        "required": [r for r in required if r != "profile"],
        "additionalProperties": False,
    }
    if write:
        schema["properties"]["client_request_id"] = {
            "type": "string", "minLength": 8, "maxLength": 120,
            "description": "Jedinečný klíč požadavku (např. UUID). Opakování se stejným klíčem "
                           "a stejnými argumenty vrátí původní výsledek a nic nezaloží znovu.",
        }
        schema["required"].append("client_request_id")
    annotations = {
        "readOnlyHint": (not write) if read_only is None else read_only,
        "destructiveHint": destructive,
        "idempotentHint": write or idempotent,
        "openWorldHint": False,
    }

    def decorator(fn):
        TOOLS[name] = Tool(name, title, description, schema, fn, write, annotations)
        return fn

    return decorator


# --- společné části schémat ---------------------------------------------------------

PROFILE = {
    "type": ["string", "integer"],
    "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. "
                   "Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej.",
}
DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$", "description": "Datum ve tvaru RRRR-MM-DD"}
AMOUNT = {"type": ["string", "number"], "description": "Částka v Kč, desetinná tečka (např. \"1936.00\")"}
LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 20}
CURSOR = {"type": "string", "description": "Hodnota next_cursor z předchozí stránky"}
METHOD = {"type": "string", "enum": [c for c, _ in PaymentMethod.choices]}
LINE = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "minLength": 1, "maxLength": 300},
        "quantity": {"type": ["string", "number"], "default": 1},
        "unit": {"type": "string", "maxLength": 12},
        "unit_price": {**AMOUNT, "description": "Cena za jednotku BEZ DPH v Kč. Pokud uživatel zadal cenu "
                                                "s DPH, zeptej se nebo ji přepočítej až po jeho potvrzení."},
        "discount_percent": {"type": ["string", "number"], "default": 0},
        "vat_rate_percent": {"type": ["string", "number"],
                             "description": "Sazba DPH z nabídky profilu (list_profiles → vat_rates). "
                                            "Výchozí je první aktivní sazba profilu."},
    },
    "required": ["description", "unit_price"],
    "additionalProperties": False,
}
DRAFT_FIELDS = {
    "partner_id": {"type": "integer", "description": "Id odběratele z search_partners (stejný profil)."},
    "lines": {"type": "array", "items": LINE, "minItems": 1, "maxItems": 100},
    "issue_date": DATE,
    "taxable_supply_date": DATE,
    "due_date": DATE,
    "due_days": {"type": "integer", "minimum": 0, "maximum": 365,
                 "description": "Splatnost ve dnech od data vystavení (místo due_date)."},
    "payment_method": METHOD,
    "bank_account_id": {"type": "integer"},
    "vat_regime": {"type": "string", "description": "Kód režimu DPH profilu (list_profiles → vat_regimes)."},
    "series_id": {"type": "integer"},
    "variable_symbol": {"type": "string", "pattern": r"^\d{1,10}$",
                        "description": "Jen u profilů s ručním VS; jinak se přidělí z čísla faktury."},
    "constant_symbol": {"type": "string", "pattern": r"^\d{1,4}$"},
    "specific_symbol": {"type": "string", "pattern": r"^\d{1,10}$"},
    "order_reference": {"type": "string", "maxLength": 100},
    "public_note": {"type": "string", "maxLength": 2000},
    "internal_note": {"type": "string", "maxLength": 2000},
}


# --- pomocné ----------------------------------------------------------------------

@dataclass
class Ctx:
    request: object
    user: object
    actor: object
    profile: object = None  # naposledy rozpoznaný profil (pro audit volání)


def _accessible(ctx):
    return list(accessible_profiles(ctx.user).order_by("name"))


def _profiles_with(ctx, perm):
    return [p for p in _accessible(ctx) if has_perm(ctx.user, p, perm)]


def resolve_profile(ctx, value, perm, *, required=True):  # noqa: C901
    """Profil přesně podle id/slug/IČO mezi přístupnými profily. Nikdy nehádá."""
    if value is None or str(value).strip() == "":
        if not required:
            return None
        raise ToolError(
            E.PROFILE_REQUIRED,
            "Operace vyžaduje výslovně určený vystavující profil. Zeptej se uživatele, za který profil ji má provést.",
            available_profiles=[profile_ref(p) for p in _profiles_with(ctx, perm)],
        )
    text = str(value).strip()
    cond = Q(slug=text) | Q(ico=text)
    if text.isdigit() and len(text) < 8:
        cond |= Q(pk=int(text))
    matches = [p for p in accessible_profiles(ctx.user).filter(cond)]
    if len(matches) != 1:
        raise ToolError(
            E.NOT_FOUND if not matches else E.AMBIGUOUS,
            f"Profil „{text}“ nebyl jednoznačně nalezen mezi profily, ke kterým máte přístup.",
            available_profiles=[profile_ref(p) for p in _accessible(ctx)],
        )
    profile = matches[0]
    if not has_perm(ctx.user, profile, perm):
        raise ToolError(E.FORBIDDEN, f"V profilu {profile.name} nemáte oprávnění {perm}.",
                        profile=profile_ref(profile))
    ctx.profile = profile
    return profile


def _read_profiles(ctx, value, perm):
    if value not in (None, ""):
        return [resolve_profile(ctx, value, perm)]
    return _profiles_with(ctx, perm)


def _date(value, name):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(E.INVALID_ARGUMENTS, f"Neplatné datum v poli {name}: {value}") from exc


def _decimal(value, name):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ToolError(E.INVALID_ARGUMENTS, f"Neplatné číslo v poli {name}: {value}") from exc


def _page(items, limit, cursor):
    try:
        offset = int(cursor or 0)
    except ValueError as exc:
        raise ToolError(E.INVALID_ARGUMENTS, "Neplatný cursor.") from exc
    limit = min(limit or 20, MAX_LIMIT)
    page = items[offset:offset + limit]
    nxt = offset + limit if offset + limit < len(items) else None
    return page, (str(nxt) if nxt is not None else None)


def _group(items, key):
    groups = {}
    for item in items:
        groups.setdefault(item["profile"]["id"], {"profile": item["profile"], key: []})[key].append(item)
    return list(groups.values())


def _invoice(profile, invoice_id):
    try:
        return (Invoice.objects.for_profile(profile).with_payment_state()
                .select_related("profile", "partner").prefetch_related("payments__created_by", "lines")
                .get(pk=invoice_id))
    except Invoice.DoesNotExist as exc:
        raise ToolError(E.NOT_FOUND, f"Doklad {invoice_id} v profilu {profile.name} neexistuje.",
                        profile=profile_ref(profile)) from exc


def _partner(profile, partner_id):
    try:
        return Partner.objects.select_related("profile").get(pk=partner_id, profile=profile)
    except Partner.DoesNotExist as exc:
        raise ToolError(E.NOT_FOUND, f"Partner {partner_id} v profilu {profile.name} neexistuje.",
                        profile=profile_ref(profile)) from exc


def _issuer_recap(invoice):
    profile = invoice.profile
    bank = invoice.bank_account
    return {
        "profile": profile_ref(profile),
        "issuer_name": profile.name,
        "issuer_ico": profile.ico,
        "bank_account": {"account_number": bank.account_number, "iban": bank.iban} if bank else None,
        "series": invoice.series.name if invoice.series_id else None,
    }


# --- čtení ------------------------------------------------------------------------

@tool("list_profiles", title="Seznam fakturačních profilů",
      description="Vrátí fakturační profily (vystavitele), ke kterým má uživatel přístup, s jeho "
                  "oprávněními, sazbami a režimy DPH, účty a číselnými řadami. Použij na začátku, "
                  "když není jasné, za koho se má dotaz nebo změna provést.",
      properties={})
def list_profiles(ctx, args):
    result = []
    for p in _accessible(ctx):
        perms = [perm for perm in (INVOICES_VIEW, INVOICES_MODIFY, PARTNERS_VIEW, PARTNERS_MODIFY)
                 if has_perm(ctx.user, p, perm)]
        result.append({
            **profile_ref(p),
            "dic": p.dic, "vat_payer": p.vat_payer, "is_active": p.is_active, "permissions": perms,
            "vat_rates": [str(r.rate_percent.normalize()) for r in p.vat_rates.filter(is_active=True)],
            "vat_regimes": [{"code": r.code, "label": r.label, "applies_vat": r.applies_vat}
                            for r in p.vat_regimes.filter(is_active=True)],
            "bank_accounts": [{"id": a.pk, "label": a.label, "account_number": a.account_number,
                               "is_default": a.pk == p.default_bank_account_id}
                              for a in p.bank_accounts.filter(is_active=True)],
            "series": [{"id": s.pk, "name": s.name, "format": s.format,
                        "is_default": s.pk == p.default_series_id}
                       for s in p.series.filter(is_active=True)],
            "default_due_days": p.default_due_days,
        })
    return {"profiles": result, "count": len(result)}


@tool("search_partners", title="Hledání partnerů",
      description="Vyhledá odběratele/dodavatele podle názvu, IČO nebo DIČ. Bez profilu hledá ve všech "
                  "přístupných profilech a výsledky rozdělí podle profilu. Stránkované.",
      properties={"query": {"type": "string", "minLength": 2, "maxLength": 100}, "profile": PROFILE,
                  "include_inactive": {"type": "boolean", "default": False}, "limit": LIMIT, "cursor": CURSOR},
      required=["query"])
def search_partners(ctx, args):
    profiles = _read_profiles(ctx, args.get("profile"), PARTNERS_VIEW)
    q = args["query"].strip()
    qs = Partner.objects.filter(profile__in=profiles).filter(
        Q(name__icontains=q) | Q(ico__startswith=q) | Q(dic__icontains=q)
    ).select_related("profile").order_by("profile__name", "name", "pk")
    if not args.get("include_inactive"):
        qs = qs.filter(is_active=True)
    page, nxt = _page(list(qs[:500]), args.get("limit"), args.get("cursor"))
    items = [partner_out(p) for p in page]
    return {"query": q, "results_by_profile": _group(items, "partners"), "returned": len(items),
            "next_cursor": nxt}


@tool("get_partner", title="Detail partnera",
      description="Detail partnera v daném profilu včetně posledních dokladů.",
      properties={"profile": PROFILE, "partner_id": {"type": "integer"}},
      required=["profile", "partner_id"])
def get_partner(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), PARTNERS_VIEW)
    partner = _partner(profile, args["partner_id"])
    data = partner_out(partner, detail=True)
    if has_perm(ctx.user, profile, INVOICES_VIEW):
        recent = (Invoice.objects.for_profile(profile).filter(partner=partner).with_payment_state()
                  .select_related("profile", "partner").prefetch_related("payments__created_by")[:10])
        data["recent_invoices"] = [invoice_out(i, ctx.request) for i in recent]
    return {"partner": data}


@tool("search_invoices", title="Hledání faktur",
      description="Vyhledá doklady (návrhy i vystavené) podle čísla, odběratele, stavu, úhrady, "
                  "splatnosti a období. Bez profilu hledá ve všech profilech s oprávněním "
                  "invoices:view a výsledky rozdělí podle profilu. Stránkované.",
      properties={
          "profile": PROFILE,
          "query": {"type": "string", "maxLength": 100, "description": "Číslo dokladu, VS nebo název odběratele"},
          "partner_id": {"type": "integer"},
          "status": {"type": "string", "enum": ["draft", "issued", "cancelled"]},
          "payment_state": {"type": "string", "enum": ["unpaid", "partial", "paid", "overpaid"]},
          "overdue_only": {"type": "boolean", "default": False},
          "issued_from": DATE, "issued_to": DATE, "due_from": DATE, "due_to": DATE,
          "limit": LIMIT, "cursor": CURSOR,
      })
def search_invoices(ctx, args):
    profiles = _read_profiles(ctx, args.get("profile"), INVOICES_VIEW)
    qs = (Invoice.objects.filter(profile__in=profiles).with_payment_state()
          .select_related("profile", "partner").prefetch_related("payments__created_by")
          .order_by("profile__name", "-issue_date", "-pk"))
    if args.get("query"):
        q = args["query"].strip()
        qs = qs.filter(Q(number__icontains=q) | Q(variable_symbol=q) | Q(partner__name__icontains=q))
    if args.get("partner_id"):
        qs = qs.filter(partner_id=args["partner_id"])
    if args.get("status"):
        qs = qs.filter(status=args["status"])
    if args.get("payment_state"):
        qs = qs.filter(payment_state=args["payment_state"]).exclude(status=InvoiceStatus.DRAFT)
    if args.get("overdue_only"):
        qs = qs.filter(is_overdue=True)
    for key, lookup in (("issued_from", "issue_date__gte"), ("issued_to", "issue_date__lte"),
                        ("due_from", "due_date__gte"), ("due_to", "due_date__lte")):
        if args.get(key):
            qs = qs.filter(**{lookup: _date(args[key], key)})
    page, nxt = _page(list(qs[:1000]), args.get("limit"), args.get("cursor"))
    items = [invoice_out(i, ctx.request) for i in page]
    return {"results_by_profile": _group(items, "invoices"), "returned": len(items), "next_cursor": nxt}


@tool("get_invoice", title="Detail dokladu",
      description="Detail dokladu: vystavitel, odběratel, položky, částky, stav úhrady včetně platby "
                  "a jejího zdroje a času, splatnost a odkazy na web/PDF.",
      properties={"profile": PROFILE, "invoice_id": {"type": "integer"}},
      required=["profile", "invoice_id"])
def get_invoice(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), INVOICES_VIEW)
    return {"invoice": invoice_out(_invoice(profile, args["invoice_id"]), ctx.request, detail=True)}


@tool("find_last_invoice", title="Poslední faktura partnera",
      description="Najde poslední vystavenou fakturu partnera a vrátí její stav úhrady (zbývající částka, "
                  "splatnost, čas a zdroj poslední platby). Pokud název odpovídá více partnerům nebo "
                  "stejnému partnerovi ve více profilech, vrátí chybu 'ambiguous' s kandidáty – "
                  "uživatele se pak zeptej a zavolej znovu s partner_id a profilem.",
      properties={"partner": {"type": ["string", "integer"], "description": "Název/IČO partnera nebo partner_id"},
                  "profile": PROFILE},
      required=["partner"])
def find_last_invoice(ctx, args):
    profiles = _read_profiles(ctx, args.get("profile"), INVOICES_VIEW)
    needle = str(args["partner"]).strip()
    base = Partner.objects.filter(profile__in=profiles).select_related("profile")
    if isinstance(args["partner"], int) or (needle.isdigit() and len(needle) < 8):
        candidates = list(base.filter(pk=int(needle)))
    else:
        candidates = list(base.filter(Q(name__icontains=needle) | Q(ico=needle)))
        exact = [p for p in candidates if p.name.lower() == needle.lower() or p.ico == needle]
        if len(exact) == 1:
            candidates = exact
    if not candidates:
        raise ToolError(E.NOT_FOUND, f"Partner „{needle}“ nebyl v přístupných profilech nalezen.")
    if len(candidates) > 1:
        raise ToolError(E.AMBIGUOUS, "Dotazu odpovídá více partnerů nebo více profilů. Upřesni s uživatelem "
                                     "partnera a vystavující profil.",
                        candidates=[partner_out(p) for p in candidates[:20]])
    partner = candidates[0]
    ctx.profile = partner.profile
    last = (Invoice.objects.for_profile(partner.profile).filter(partner=partner)
            .exclude(status=InvoiceStatus.DRAFT).with_payment_state()
            .select_related("profile", "partner").prefetch_related("payments__created_by")
            .order_by("-issue_date", "-issued_at", "-pk").first())
    result = {"profile": profile_ref(partner.profile), "partner": partner_out(partner), "invoice": None}
    if last is None:
        result["message"] = "Partner zatím nemá žádnou vystavenou fakturu."
    else:
        result["invoice"] = invoice_out(last, ctx.request)
    return result


@tool("get_profile_summary", title="Souhrn profilu",
      description="Souhrny jednoho profilu v CZK: vystaveno, uhrazeno, zbývá, po splatnosti, rozpracované "
                  "návrhy a faktury k upomenutí. Částky různých profilů se nikdy nesčítají.",
      properties={"profile": PROFILE, "year": {"type": "integer", "minimum": 2000, "maximum": 2100}},
      required=["profile"])
def get_profile_summary(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), INVOICES_VIEW)
    s = reports.summary(profile, args.get("year"))
    due = inv.reminders_due(profile)
    return {
        "profile": profile_ref(profile), "year": args.get("year"), "currency": "CZK",
        "issued_count": s["count"], "issued": money(s["issued"]), "paid": money(s["paid"]),
        "remaining": money(s["remaining"]), "overdue": money(s["overdue"]),
        "overdue_count": s["overdue_count"], "drafts": s["drafts"],
        "reminders_due": [{"invoice_id": d["invoice"].pk, "number": d["invoice"].number,
                           "partner": d["invoice"].partner.name, "overdue_days": d["overdue_days"],
                           "rule": d["rule"].label} for d in due],
    }


@tool("lookup_company_ares", title="Vyhledání firmy v ARES",
      description="Dohledá ekonomický subjekt v registru ARES podle IČO nebo názvu (jen čtení, nic "
                  "neukládá). Výsledek lze předat do create_partner.",
      properties={"ico": {"type": "string", "pattern": r"^\d{1,8}$"},
                  "name": {"type": "string", "minLength": 3, "maxLength": 100}},
      read_only=True)
def lookup_company_ares(ctx, args):
    if not args.get("ico") and not args.get("name"):
        raise ToolError(E.INVALID_ARGUMENTS, "Zadej IČO nebo název firmy.")
    try:
        if args.get("ico"):
            found = ares.lookup(args["ico"])
            if found is None:
                raise ToolError(E.NOT_FOUND, f"IČO {args['ico']} v ARES neexistuje.")
            return {"source": "ARES", "results": [found], "total": 1}
        data = ares.search(args["name"])
        return {"source": "ARES", **data}
    except ares.AresError as exc:
        raise ToolError(E.EXTERNAL_SERVICE, str(exc)) from exc


@tool("preview_invoice_draft", title="Náhled návrhu",
      description="Vygeneruje náhled návrhu faktury (bez čísla, s vodoznakem NÁHLED) a vrátí rekapitulaci "
                  "a odkaz na PDF. include_pdf=true přiloží PDF přímo do odpovědi.",
      properties={"profile": PROFILE, "invoice_id": {"type": "integer"},
                  "include_pdf": {"type": "boolean", "default": False}},
      required=["profile", "invoice_id"], read_only=True)
def preview_invoice_draft(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), INVOICES_VIEW)
    invoice = _invoice(profile, args["invoice_id"])
    if invoice.status != InvoiceStatus.DRAFT:
        raise ToolError(E.VALIDATION_ERROR, "Doklad už je vystavený – použij get_invoice a pdf_url.",
                        invoice=invoice_out(invoice, ctx.request))
    doc = inv.build_preview(profile, inv.header_of(invoice), inv.lines_of(invoice))
    result = {"invoice": invoice_out(invoice, ctx.request, detail=True), **_issuer_recap(invoice),
              "qr_payment": (doc["qr"] or {}).get("payload") or (doc["qr"] or {}).get("error")}
    if args.get("include_pdf"):
        result["_content"] = [{
            "type": "resource",
            "resource": {"uri": result["invoice"]["preview_url"], "mimeType": "application/pdf",
                         "blob": base64.b64encode(pdf.render_pdf(doc)).decode()},
        }]
    return result


# --- zápisy -------------------------------------------------------------------------

def _service_errors(fn):
    from accounts.permissions import AccessDenied
    from core.concurrency import ConcurrentUpdate

    try:
        return fn()
    except AccessDenied as exc:
        raise ToolError(E.FORBIDDEN, "Nemáte oprávnění k této operaci.") from exc
    except ConcurrentUpdate as exc:
        raise ToolError(E.VERSION_CONFLICT, exc.message) from exc
    except ValidationError as exc:
        msgs = ([m for v in exc.message_dict.values() for m in v] if hasattr(exc, "message_dict")
                else exc.messages)
        raise ToolError(E.VALIDATION_ERROR, "; ".join(msgs), messages=msgs) from exc


@tool("create_partner", title="Založení partnera",
      description="Založí odběratele ve výslovně zvoleném profilu. S IČO se údaje načtou z ARES "
                  "(use_ares=true); výslovně zadaná pole mají přednost. Pokud partner se stejným IČO "
                  "v profilu existuje, vrátí chybu 'duplicate' s existujícím záznamem.",
      properties={
          "profile": PROFILE, "ico": {"type": "string", "pattern": r"^\d{1,8}$"},
          "name": {"type": "string", "maxLength": 200}, "dic": {"type": "string", "maxLength": 14},
          "street": {"type": "string", "maxLength": 200}, "city": {"type": "string", "maxLength": 100},
          "zip_code": {"type": "string", "maxLength": 10}, "country": {"type": "string", "maxLength": 60},
          "vat_payer": {"type": "boolean"}, "billing_email": {"type": "string", "maxLength": 254},
          "contact_person": {"type": "string", "maxLength": 200}, "phone": {"type": "string", "maxLength": 40},
          "due_days_override": {"type": "integer", "minimum": 0, "maximum": 365},
          "use_ares": {"type": "boolean", "default": True},
          "allow_duplicate": {"type": "boolean", "default": False},
      },
      required=["profile"], write=True)
def create_partner(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), PARTNERS_MODIFY)
    data = {}
    source = "manual"
    ico = ares.normalize_ico(args.get("ico", "")) if args.get("ico") else ""
    if ico and args.get("use_ares", True):
        try:
            found = ares.lookup(ico)
        except ares.AresError as exc:
            raise ToolError(E.EXTERNAL_SERVICE, str(exc)) from exc
        if found is None:
            raise ToolError(E.NOT_FOUND, f"IČO {ico} v ARES neexistuje. Zadej údaje ručně s use_ares=false.")
        if found["dissolved_on"]:
            raise ToolError(E.VALIDATION_ERROR, f"Subjekt s IČO {ico} zanikl {found['dissolved_on']}.")
        data = {k: v for k, v in found.items() if k not in {"address_text", "dissolved_on"}}
        source = "ARES"
    fields = ["name", "dic", "street", "city", "zip_code", "country", "vat_payer", "billing_email",
              "contact_person", "phone", "due_days_override"]
    data.update({k: args[k] for k in fields if k in args})
    if ico:
        data["ico"] = ico
    if not data.get("name"):
        raise ToolError(E.INVALID_ARGUMENTS, "Chybí název partnera (zadej name nebo IČO s use_ares).")
    partner = Partner(profile=profile, **data)
    dupes = list(partner_services.duplicates(profile, partner))
    if dupes and not args.get("allow_duplicate"):
        raise ToolError("duplicate", "V profilu už existuje podobný partner.",
                        existing=[partner_out(p) for p in dupes])
    try:
        partner.full_clean(exclude=["profile"])
    except ValidationError as exc:
        raise ToolError(E.VALIDATION_ERROR, "; ".join(exc.messages), messages=exc.messages) from exc
    _service_errors(lambda: partner_services.save_partner(ctx.actor, profile, partner))
    return {"created": True, "source": source, "partner": partner_out(partner, detail=True)}


def _build_header(profile, args, base=None):
    """Hlavička návrhu z argumentů; hodnoty mimo profil se odmítnou (nic se nehádá)."""
    head = dict(base or {})
    today = timezone.localdate()
    if base is None:
        head.update({
            "series": profile.default_series, "vat_regime": profile.default_vat_regime,
            "issue_date": today, "taxable_supply_date": today, "due_date": None,
            "payment_method": profile.default_payment_method, "bank_account": profile.default_bank_account,
            "variable_symbol": "", "constant_symbol": profile.constant_symbol, "specific_symbol": "",
            "order_reference": "", "public_note": "", "internal_note": "",
        })
    if "partner_id" in args:
        head["partner"] = _partner(profile, args["partner_id"])
        if base is None and head["partner"].payment_method_override:
            head["payment_method"] = head["partner"].payment_method_override
    for key in ("issue_date", "taxable_supply_date", "due_date"):
        if key in args:
            head[key] = _date(args[key], key)
    if "due_days" in args:
        head["due_date"] = head["issue_date"] + timedelta(days=args["due_days"])
    if "payment_method" in args:
        head["payment_method"] = args["payment_method"]
    if "bank_account_id" in args:
        head["bank_account"] = BankAccount.objects.filter(
            profile=profile, pk=args["bank_account_id"], is_active=True).first()
        if head["bank_account"] is None:
            raise ToolError(E.NOT_FOUND, "Bankovní účet v profilu neexistuje nebo je neaktivní.")
    if "vat_regime" in args:
        head["vat_regime"] = VatRegime.objects.filter(profile=profile, code=args["vat_regime"], is_active=True).first()
        if head["vat_regime"] is None:
            raise ToolError(E.NOT_FOUND, f"Režim DPH „{args['vat_regime']}“ profil nemá.")
    if "series_id" in args:
        head["series"] = NumberSeries.objects.filter(profile=profile, pk=args["series_id"], is_active=True).first()
        if head["series"] is None:
            raise ToolError(E.NOT_FOUND, "Číselná řada v profilu neexistuje nebo je neaktivní.")
    if "variable_symbol" in args:
        if profile.vs_rule == VsRule.INVOICE_NUMBER:
            raise ToolError(E.INVALID_ARGUMENTS, "Profil přiděluje VS z čísla faktury – nezadávej ho.")
        head["variable_symbol"] = args["variable_symbol"]
    for key in ("constant_symbol", "specific_symbol", "order_reference", "public_note", "internal_note"):
        if key in args:
            head[key] = args[key]
    if head.get("partner") is None:
        raise ToolError(E.INVALID_ARGUMENTS, "Chybí partner_id odběratele (najdi ho přes search_partners).")
    if head.get("series") is None or head.get("vat_regime") is None:
        raise ToolError(E.VALIDATION_ERROR, "Profil nemá výchozí číselnou řadu nebo režim DPH.")
    return head


def _build_lines(profile, raw_lines):
    rates = {r.rate_percent.normalize(): r.rate_percent for r in profile.vat_rates.filter(is_active=True)}
    default_rate = next(iter(profile.vat_rates.filter(is_active=True)), None)
    lines = []
    for i, raw in enumerate(raw_lines, start=1):
        rate_in = raw.get("vat_rate_percent")
        if rate_in in (None, ""):
            rate = default_rate.rate_percent if default_rate else Decimal("0")
        else:
            rate = rates.get(_decimal(rate_in, f"lines[{i}].vat_rate_percent").normalize())
            if rate is None:
                raise ToolError(E.VALIDATION_ERROR, f"Položka {i}: sazbu DPH {rate_in} % profil nenabízí.",
                                allowed_rates=[str(r) for r in rates])
        quantity = _decimal(raw.get("quantity", 1), f"lines[{i}].quantity")
        if quantity <= 0:
            raise ToolError(E.VALIDATION_ERROR, f"Položka {i}: množství musí být kladné.")
        lines.append({
            "description": raw["description"].strip(), "quantity": quantity, "unit": raw.get("unit", ""),
            "unit_price": _decimal(raw["unit_price"], f"lines[{i}].unit_price").quantize(Decimal("0.01")),
            "discount_percent": _decimal(raw.get("discount_percent", 0), f"lines[{i}].discount_percent"),
            "vat_rate_percent": rate,
        })
    return lines


def _draft_result(ctx, invoice, message):
    invoice = _invoice(invoice.profile, invoice.pk)
    return {"message": message, "invoice": invoice_out(invoice, ctx.request, detail=True),
            **_issuer_recap(invoice), "issue_problems": inv.issue_problems(invoice)}


@tool("create_invoice_draft", title="Vytvoření návrhu faktury",
      description="Vytvoří NÁVRH faktury (nevystaví ho, nepřidělí číslo) ve výslovně zvoleném profilu a "
                  "vrátí rekapitulaci včetně vystavitele, účtu a odkazu na náhled. Ceny jsou bez DPH.",
      properties={"profile": PROFILE, **DRAFT_FIELDS},
      required=["profile", "partner_id", "lines"], write=True, read_only=False)
def create_invoice_draft(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), INVOICES_MODIFY)
    header = _build_header(profile, args)
    lines = _build_lines(profile, args["lines"])
    invoice = _service_errors(lambda: inv.create_draft(ctx.actor, profile, header, lines))
    return _draft_result(ctx, invoice, "Vytvořen NÁVRH faktury. Doklad zatím není vystavený.")


@tool("update_invoice_draft", title="Úprava návrhu faktury",
      description="Upraví návrh faktury. Uvedená pole se přepíšou, lines (pokud jsou uvedeny) nahradí "
                  "všechny položky. Vyžaduje expected_version z posledního načtení (optimistický zámek). "
                  "Vystavený doklad upravit nelze.",
      properties={"profile": PROFILE, "invoice_id": {"type": "integer"},
                  "expected_version": {"type": "integer"}, **DRAFT_FIELDS},
      required=["profile", "invoice_id", "expected_version"], write=True)
def update_invoice_draft(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), INVOICES_MODIFY)
    invoice = _invoice(profile, args["invoice_id"])
    if invoice.status != InvoiceStatus.DRAFT:
        raise ToolError(E.VALIDATION_ERROR, "Vystavený doklad nelze upravit.", invoice=invoice_out(invoice, ctx.request))
    header = _build_header(profile, args, base=inv.header_of(invoice))
    lines = _build_lines(profile, args["lines"]) if "lines" in args else inv.lines_of(invoice)
    invoice = _service_errors(lambda: inv.update_draft(ctx.actor, invoice, args["expected_version"], header, lines))
    return _draft_result(ctx, invoice, "Návrh upraven. Doklad zatím není vystavený.")


def _require_confirm(ctx, args, invoice, action):
    if args.get("confirm") is not True:
        raise ToolError(E.CONFIRMATION_REQUIRED,
                        f"{action} je nevratná operace. Ukaž uživateli rekapitulaci a zavolej znovu s confirm=true "
                        "až po jeho výslovném souhlasu.",
                        recap={**_issuer_recap(invoice), "invoice": invoice_out(invoice, ctx.request)})


@tool("issue_invoice", title="Vystavení faktury",
      description="Vystaví návrh: přidělí číslo a VS, zmrazí údaje a uloží PDF. Nevratné – volej jen na "
                  "výslovný pokyn uživatele po ukázání rekapitulace, s confirm=true. Bez confirm vrátí "
                  "rekapitulaci (chyba confirmation_required).",
      properties={"profile": PROFILE, "invoice_id": {"type": "integer"}, "expected_version": {"type": "integer"},
                  "confirm": {"type": "boolean", "description": "true = uživatel výslovně potvrdil vystavení"}},
      required=["profile", "invoice_id", "expected_version"], write=True, destructive=True)
def issue_invoice(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), INVOICES_MODIFY)
    invoice = _invoice(profile, args["invoice_id"])
    if invoice.status != InvoiceStatus.DRAFT:
        raise ToolError(E.VALIDATION_ERROR, "Doklad už je vystavený nebo stornovaný.",
                        invoice=invoice_out(invoice, ctx.request))
    if args.get("confirm") is not True:
        recap = {**_issuer_recap(invoice), "invoice": invoice_out(invoice, ctx.request),
                 "expected_number": inv.peek_next_number(invoice.series, invoice.issue_date),
                 "issue_problems": inv.issue_problems(invoice)}
        raise ToolError(E.CONFIRMATION_REQUIRED,
                        "Vystavení je nevratné. Ukaž uživateli rekapitulaci (vystavitel, IČO, účet, řada, "
                        "částka) a po jeho souhlasu zavolej znovu s confirm=true.", recap=recap)
    issued = _service_errors(lambda: inv.issue(ctx.actor, invoice, args["expected_version"]))
    issued = _invoice(profile, issued.pk)
    return {"message": f"Faktura {issued.number} byla vystavena.", **_issuer_recap(issued),
            "invoice": invoice_out(issued, ctx.request, detail=True)}


@tool("cancel_invoice", title="Storno faktury",
      description="Stornuje vystavenou fakturu bez evidované úhrady (číslo se znovu nepoužije). Nevratné – "
                  "jen na výslovný pokyn uživatele s důvodem a confirm=true.",
      properties={"profile": PROFILE, "invoice_id": {"type": "integer"}, "expected_version": {"type": "integer"},
                  "reason": {"type": "string", "minLength": 3, "maxLength": 500}, "confirm": {"type": "boolean"}},
      required=["profile", "invoice_id", "expected_version", "reason"], write=True, destructive=True)
def cancel_invoice(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), INVOICES_MODIFY)
    invoice = _invoice(profile, args["invoice_id"])
    _require_confirm(ctx, args, invoice, "Storno")
    cancelled = _service_errors(lambda: inv.cancel(ctx.actor, invoice, args["expected_version"], args["reason"]))
    cancelled = _invoice(profile, cancelled.pk)
    return {"message": f"Faktura {cancelled.number} byla stornována.", **_issuer_recap(cancelled),
            "invoice": invoice_out(cancelled, ctx.request)}


@tool("record_payment", title="Zápis platby",
      description="Zapíše přijatou platbu k vystavené faktuře (částečnou, plnou i přeplatek) a vrátí nový "
                  "stav úhrady. Opravu chybné platby řeší protioperace ve webu.",
      properties={"profile": PROFILE, "invoice_id": {"type": "integer"}, "amount": AMOUNT, "paid_on": DATE,
                  "method": METHOD, "reference": {"type": "string", "maxLength": 100},
                  "note": {"type": "string", "maxLength": 255}},
      required=["profile", "invoice_id", "amount", "paid_on"], write=True)
def record_payment(ctx, args):
    profile = resolve_profile(ctx, args.get("profile"), INVOICES_MODIFY)
    invoice = _invoice(profile, args["invoice_id"])
    amount = _decimal(args["amount"], "amount").quantize(Decimal("0.01"))
    payment = _service_errors(lambda: inv.add_payment(
        ctx.actor, invoice, amount=amount, paid_on=_date(args["paid_on"], "paid_on"),
        method=args.get("method") or invoice.payment_method, reference=args.get("reference", ""),
        note=args.get("note", "")))
    updated = _invoice(profile, invoice.pk)
    return {"message": f"Platba {money(payment.amount)} Kč zapsána k faktuře {updated.number}.",
            "payment_id": payment.pk, "invoice": invoice_out(updated, ctx.request)}
