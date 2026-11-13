"""Business logika faktur. Každá operace sama ověřuje oprávnění a zapisuje audit,
takže web, příkazová řádka i budoucí MCP jdou stejnou cestou."""

import hashlib
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.permissions import INVOICES_MODIFY, require
from audit import services as audit
from core.choices import PaymentMethod
from core.concurrency import bump, lock_and_check
from profiles import numbering
from profiles.models import NumberCounter, NumberSeries, VsRule

from . import document, pdf
from .models import (
    DeliveryRecord,
    DeliveryStatus,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Payment,
    ReminderRecord,
)

HEADER_FIELDS = [
    "partner", "series", "issue_date", "taxable_supply_date", "due_date", "payment_method",
    "bank_account", "vat_regime", "variable_symbol", "constant_symbol", "specific_symbol",
    "order_reference", "public_note", "internal_note",
]
LINE_FIELDS = ["description", "quantity", "unit", "unit_price", "discount_percent", "vat_rate_percent"]


# --- pomocné -----------------------------------------------------------------

def default_due_date(profile, partner, issue_date):
    days = profile.default_due_days
    if partner is not None and partner.due_days_override is not None:
        days = partner.due_days_override
    return issue_date + timedelta(days=days)


def _validate_header(profile, header):
    errors = {}
    partner = header.get("partner")
    if partner is None or partner.profile_id != profile.pk:
        errors["partner"] = "Odběratel nepatří k tomuto profilu."
    for key in ("series", "vat_regime", "bank_account"):
        obj = header.get(key)
        if obj is not None and obj.profile_id != profile.pk:
            errors[key] = "Hodnota nepatří k tomuto profilu."
    if header.get("payment_method") == PaymentMethod.BANK_TRANSFER and not header.get("bank_account"):
        errors["bank_account"] = "Pro převod vyberte bankovní účet profilu."
    if header.get("due_date") and header.get("issue_date") and header["due_date"] < header["issue_date"]:
        errors["due_date"] = "Splatnost nesmí být dříve než datum vystavení."
    if errors:
        raise ValidationError(errors)


def _totals(profile, regime, lines):
    return document.compute(
        lines,
        applies_vat=regime.applies_vat and profile.vat_payer,
        method=profile.vat_calc_method,
        rounding_mode=profile.rounding_mode,
    )


def _apply_header(invoice, header):
    for key in HEADER_FIELDS:
        if key in header:
            setattr(invoice, key, header[key])
    if not invoice.due_date:
        invoice.due_date = default_due_date(invoice.profile, invoice.partner, invoice.issue_date)


def _save_lines(invoice, lines):
    totals = _totals(invoice.profile, invoice.vat_regime, lines)
    InvoiceLine.objects.filter(invoice=invoice).delete()
    InvoiceLine.objects.bulk_create(
        InvoiceLine(
            invoice=invoice,
            position=index,
            line_base=result.base,
            line_vat=result.vat,
            line_total=result.total,
            **{**{k: line.get(k) for k in LINE_FIELDS}, "vat_rate_percent": result.vat_rate_percent,
               "unit": line.get("unit") or "", "discount_percent": line.get("discount_percent") or 0},
        )
        for index, (line, result) in enumerate(zip(lines, totals.lines, strict=True))
    )
    invoice.total_base = totals.total_base
    invoice.total_vat = totals.total_vat
    invoice.rounding = totals.rounding
    invoice.total = totals.total
    invoice.vat_summary = [row.as_dict() for row in totals.vat_rows]


def lines_of(invoice):
    return [{k: getattr(line, k) for k in LINE_FIELDS} for line in invoice.lines.all()]


def header_of(invoice):
    return {k: getattr(invoice, k) for k in HEADER_FIELDS}


# --- návrh -------------------------------------------------------------------

@transaction.atomic
def create_draft(actor, profile, header, lines):
    require(actor.user, profile, INVOICES_MODIFY)
    if not profile.is_active:
        raise ValidationError("Profil je deaktivovaný – nelze vytvářet nové doklady.")
    _validate_header(profile, header)
    invoice = Invoice(profile=profile, created_by=actor.user, updated_by=actor.user)
    _apply_header(invoice, header)
    invoice.save()
    _save_lines(invoice, lines)
    invoice.save()
    audit.record(actor, "invoice.draft_create", profile=profile, obj=invoice,
                 after=audit.snapshot(invoice))
    return invoice


@transaction.atomic
def update_draft(actor, invoice, expected_version, header, lines):
    require(actor.user, invoice.profile, INVOICES_MODIFY)
    invoice = lock_and_check(Invoice.objects.for_profile(invoice.profile), invoice.pk, expected_version)
    if not invoice.is_draft:
        raise ValidationError("Upravovat lze jen návrh.")
    _validate_header(invoice.profile, header)
    before = audit.snapshot(invoice)
    _apply_header(invoice, header)
    _save_lines(invoice, lines)
    invoice.updated_by = actor.user
    bump(invoice).save()
    audit.record(actor, "invoice.draft_update", profile=invoice.profile, obj=invoice,
                 **dict(zip(("before", "after"), audit.diff(before, audit.snapshot(invoice)), strict=True)))
    return invoice


@transaction.atomic
def delete_draft(actor, invoice, expected_version):
    require(actor.user, invoice.profile, INVOICES_MODIFY)
    invoice = lock_and_check(Invoice.objects.for_profile(invoice.profile), invoice.pk, expected_version)
    if not invoice.is_draft:
        raise ValidationError("Smazat lze jen návrh.")
    audit.record(actor, "invoice.draft_delete", profile=invoice.profile, obj=invoice,
                 before=audit.snapshot(invoice))
    invoice.delete()


# --- náhled ------------------------------------------------------------------

def build_preview(profile, header, lines):
    """Data náhledu z neuložených údajů – nic neukládá a nečerpá číslo."""
    regime = header["vat_regime"]
    totals = _totals(profile, regime, lines)
    partner = header["partner"]
    head = dict(header)
    if not head.get("due_date"):
        head["due_date"] = default_due_date(profile, partner, head["issue_date"])
    if profile.vs_rule == VsRule.INVOICE_NUMBER:
        head["variable_symbol"] = ""
    return document.build(
        issuer=document.issuer_snapshot(profile),
        partner=document.partner_snapshot(partner),
        bank=document.bank_snapshot(header.get("bank_account")),
        regime=document.regime_snapshot(regime),
        header=head,
        lines=lines,
        totals=totals,
        preview=True,
    )


def issued_document(invoice):
    """Data dokladu vystavené faktury výhradně ze snímků a uložených položek."""
    lines = lines_of(invoice)
    regime = invoice.vat_regime_snapshot
    totals = document.compute(
        lines,
        applies_vat=regime["applies_vat"] and invoice.issuer_snapshot["vat_payer"],
        method=invoice.issuer_snapshot.get("vat_calc_method", "per_line"),
        rounding_mode=invoice.issuer_snapshot.get("rounding_mode", "none"),
    )
    return document.build(
        issuer=invoice.issuer_snapshot,
        partner=invoice.partner_snapshot,
        bank=invoice.bank_snapshot,
        regime=regime,
        header={k: getattr(invoice, k) for k in (
            "issue_date", "taxable_supply_date", "due_date", "payment_method", "variable_symbol",
            "constant_symbol", "specific_symbol", "order_reference", "public_note")},
        lines=lines,
        totals=totals,
        number=invoice.number,
        issued_by={"name": invoice.issued_by.name, "email": invoice.issued_by.email}
        if invoice.issued_by else None,
    )


# --- číslování ----------------------------------------------------------------

def peek_next_number(series, issue_date):
    """Očekávané číslo (jen informativní, nic nerezervuje)."""
    period = numbering.period_for(series.format, issue_date.year)
    counter = NumberCounter.objects.filter(series=series, period=period).first()
    value = series.start_value if counter is None or counter.last_value is None else counter.last_value + 1
    return numbering.render(series.format, value, issue_date.year, series.min_width)


def allocate_number(series, issue_date):
    """Atomicky přidělí další číslo. Volat uvnitř transakce."""
    series = NumberSeries.objects.select_for_update().get(pk=series.pk)
    period = numbering.period_for(series.format, issue_date.year)
    counter, _ = NumberCounter.objects.get_or_create(
        series=series, period=period, defaults={"last_value": None}
    )
    counter = NumberCounter.objects.select_for_update().get(pk=counter.pk)
    value = series.start_value if counter.last_value is None else counter.last_value + 1
    counter.last_value = value
    counter.save(update_fields=["last_value", "updated_at"])
    return numbering.render(series.format, value, issue_date.year, series.min_width)


# --- vystavení -----------------------------------------------------------------

def _issue_checks(invoice):
    profile = invoice.profile
    problems = []
    if not invoice.is_draft:
        problems.append("Vystavit lze jen návrh.")
    if not profile.is_active:
        problems.append("Profil je deaktivovaný.")
    if not invoice.partner.is_active:
        problems.append("Odběratel je neaktivní.")
    if not invoice.series.is_active:
        problems.append("Číselná řada je neaktivní.")
    if invoice.bank_account and (
        invoice.bank_account.profile_id != profile.pk or not invoice.bank_account.is_active
    ):
        problems.append("Bankovní účet nepatří k profilu nebo je neaktivní.")
    if invoice.payment_method == PaymentMethod.BANK_TRANSFER and not invoice.bank_account:
        problems.append("Pro úhradu převodem chybí bankovní účet.")
    if not invoice.lines.exists():
        problems.append("Faktura nemá žádnou položku.")
    if invoice.total <= 0:
        problems.append("Celková částka musí být kladná.")
    return problems


def issue_problems(invoice):
    return _issue_checks(invoice)


def _store_pdf(invoice, pdf_bytes):
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    safe_number = numbering.digits_only(invoice.number) or str(invoice.pk)
    relative = Path("invoices") / str(invoice.profile_id) / str(invoice.issue_date.year) / (
        f"{safe_number}-{sha[:8]}.pdf"
    )
    target = Path(settings.MEDIA_ROOT) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_bytes(pdf_bytes)
    tmp.replace(target)
    return str(relative), sha, target


def issue(actor, invoice, expected_version):
    """Vystaví návrh: číslo, VS, snímky, zmrazené součty, uložené PDF. Vše, nebo nic."""
    require(actor.user, invoice.profile, INVOICES_MODIFY)
    written = None
    try:
        with transaction.atomic():
            invoice = lock_and_check(
                Invoice.objects.for_profile(invoice.profile).select_related(
                    "profile", "partner", "series", "bank_account", "vat_regime"
                ),
                invoice.pk,
                expected_version,
            )
            problems = _issue_checks(invoice)
            if problems:
                raise ValidationError(problems)
            profile = invoice.profile
            # Přepočet položek ze současného nastavení profilu (návrh mohl vzniknout dříve).
            _save_lines(invoice, lines_of(invoice))
            invoice.number = allocate_number(invoice.series, invoice.issue_date)
            if profile.vs_rule == VsRule.INVOICE_NUMBER:
                vs = numbering.digits_only(invoice.number)
                if not vs or len(vs) > 10:
                    raise ValidationError("Z čísla faktury nelze odvodit variabilní symbol (1–10 číslic).")
                invoice.variable_symbol = vs
            elif not invoice.variable_symbol:
                raise ValidationError("Vyplňte variabilní symbol.")
            if not invoice.constant_symbol and profile.constant_symbol:
                invoice.constant_symbol = profile.constant_symbol
            issuer = document.issuer_snapshot(profile)
            issuer["vat_calc_method"] = profile.vat_calc_method
            issuer["rounding_mode"] = profile.rounding_mode
            invoice.issuer_snapshot = issuer
            invoice.partner_snapshot = document.partner_snapshot(invoice.partner)
            invoice.bank_snapshot = document.bank_snapshot(invoice.bank_account)
            invoice.vat_regime_snapshot = document.regime_snapshot(invoice.vat_regime)
            invoice.issued_at = timezone.now()
            invoice.issued_by = actor.user
            invoice.updated_by = actor.user
            # Uložit čísla a snímky ještě jako návrh, pak z nich sestavit PDF.
            bump(invoice).save()
            doc = issued_document(invoice)
            pdf_bytes = pdf.render_pdf(doc)
            relative, sha, written = _store_pdf(invoice, pdf_bytes)
            invoice.pdf_file = relative
            invoice.pdf_sha256 = sha
            invoice.data_hash = document.data_hash(doc)
            invoice.status = InvoiceStatus.ISSUED
            bump(invoice).save()
            audit.record(
                actor, "invoice.issue", profile=profile, obj=invoice,
                after={"number": invoice.number, "total": str(invoice.total),
                       "variable_symbol": invoice.variable_symbol, "pdf_sha256": sha},
            )
    except IntegrityError as exc:
        if written:
            written.unlink(missing_ok=True)
        raise ValidationError(
            "Číslo dokladu v této řadě už existuje. Zkontrolujte nastavení číselné řady."
        ) from exc
    except Exception:
        if written:
            written.unlink(missing_ok=True)
        raise
    return invoice


# --- storno ------------------------------------------------------------------

@transaction.atomic
def cancel(actor, invoice, expected_version, reason):
    require(actor.user, invoice.profile, INVOICES_MODIFY)
    if not (reason or "").strip():
        raise ValidationError("Uveďte důvod storna.")
    invoice = lock_and_check(Invoice.objects.for_profile(invoice.profile), invoice.pk, expected_version)
    if invoice.status != InvoiceStatus.ISSUED:
        raise ValidationError("Stornovat lze jen vystavenou fakturu.")
    if invoice.payments.exists() and paid_total(invoice) != 0:
        raise ValidationError("Fakturu s evidovanou úhradou nelze stornovat. Nejdřív platbu opravte.")
    invoice.status = InvoiceStatus.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.cancelled_by = actor.user
    invoice.cancel_reason = reason.strip()
    invoice.updated_by = actor.user
    bump(invoice).save()
    audit.record(actor, "invoice.cancel", profile=invoice.profile, obj=invoice,
                 before={"status": "issued"}, after={"status": "cancelled"}, note=invoice.cancel_reason)
    return invoice


# --- platby, předání, upomínky ----------------------------------------------

def paid_total(invoice):
    return sum((p.amount for p in invoice.payments.all()), Decimal("0.00"))


@transaction.atomic
def add_payment(actor, invoice, *, amount, paid_on, method, reference="", note=""):
    require(actor.user, invoice.profile, INVOICES_MODIFY)
    invoice = Invoice.objects.for_profile(invoice.profile).select_for_update().get(pk=invoice.pk)
    if invoice.status != InvoiceStatus.ISSUED:
        raise ValidationError("Platbu lze zapsat jen k vystavené faktuře.")
    if amount is None or amount <= 0:
        raise ValidationError("Částka platby musí být kladná.")
    payment = Payment.objects.create(
        invoice=invoice, amount=amount, paid_on=paid_on, method=method,
        reference=reference, note=note, created_by=actor.user,
    )
    audit.record(actor, "payment.create", profile=invoice.profile, obj=payment,
                 after=audit.snapshot(payment))
    return payment


@transaction.atomic
def reverse_payment(actor, payment, note=""):
    invoice = payment.invoice
    require(actor.user, invoice.profile, INVOICES_MODIFY)
    payment = Payment.objects.select_for_update().get(pk=payment.pk, invoice__profile=invoice.profile)
    if payment.reverses_id or payment.amount <= 0:
        raise ValidationError("Protioperaci nelze znovu opravit.")
    if hasattr(payment, "reversal"):
        raise ValidationError("Platba už byla zrušena.")
    reversal = Payment.objects.create(
        invoice=invoice, amount=-payment.amount, paid_on=timezone.localdate(),
        method=payment.method, reference=payment.reference,
        note=note or f"Zrušení chybně zadané platby #{payment.pk}",
        reverses=payment, created_by=actor.user,
    )
    audit.record(actor, "payment.reverse", profile=invoice.profile, obj=reversal,
                 before=audit.snapshot(payment), after=audit.snapshot(reversal), note=note)
    return reversal


@transaction.atomic
def mark_delivered(actor, invoice, *, delivered_on, note=""):
    require(actor.user, invoice.profile, INVOICES_MODIFY)
    invoice = Invoice.objects.for_profile(invoice.profile).select_for_update().get(pk=invoice.pk)
    if invoice.status == InvoiceStatus.DRAFT:
        raise ValidationError("Předat lze jen vystavenou fakturu.")
    record = DeliveryRecord.objects.create(
        invoice=invoice, delivered_on=delivered_on, note=note, created_by=actor.user
    )
    if invoice.delivery_status != DeliveryStatus.DELIVERED_OUTSIDE:
        invoice.delivery_status = DeliveryStatus.DELIVERED_OUTSIDE
        invoice.updated_by = actor.user
        bump(invoice).save()
    audit.record(actor, "invoice.delivered", profile=invoice.profile, obj=invoice,
                 after={"delivered_on": delivered_on.isoformat(), "note": note})
    return record


@transaction.atomic
def record_reminder(actor, invoice, *, reminded_on, method="", result_note=""):
    require(actor.user, invoice.profile, INVOICES_MODIFY)
    invoice = Invoice.objects.for_profile(invoice.profile).get(pk=invoice.pk)
    if invoice.status != InvoiceStatus.ISSUED:
        raise ValidationError("Upomínku lze zaznamenat jen u vystavené faktury.")
    record = ReminderRecord.objects.create(
        invoice=invoice, reminded_on=reminded_on, method=method, result_note=result_note,
        created_by=actor.user,
    )
    audit.record(actor, "invoice.reminder", profile=invoice.profile, obj=record,
                 after=audit.snapshot(record))
    return record


def reminders_due(profile, today=None):
    """Vystavené faktury po splatnosti, u kterých nastal termín další upomínky."""
    today = today or timezone.localdate()
    rules = list(profile.reminder_rules.all())
    if not rules:
        return []
    invoices = (
        Invoice.objects.for_profile(profile)
        .with_payment_state(today)
        .filter(is_overdue=True)
        .prefetch_related("reminders")
        .select_related("partner")
    )
    due = []
    for inv in invoices:
        overdue_days = (today - inv.due_date).days
        done = len(inv.reminders.all())
        reached = [r for r in rules if overdue_days >= r.days_after_due]
        if len(reached) > done:
            due.append({"invoice": inv, "rule": reached[done], "overdue_days": overdue_days})
    return due
