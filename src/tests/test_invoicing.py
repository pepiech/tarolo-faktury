import threading
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from accounts.permissions import AccessDenied, Actor
from audit.models import AuditEvent
from conftest import header_for, line
from core.concurrency import ConcurrentUpdate
from invoicing import services
from invoicing.models import Invoice, InvoiceLine, Payment
from invoicing.spayd import parse_spayd
from profiles.models import NumberCounter, NumberSeries

pytestmark = pytest.mark.django_db


def issue(actor, invoice):
    invoice.refresh_from_db()
    return services.issue(actor, invoice, invoice.version)


# --- návrh a náhled ------------------------------------------------------------

def test_draft_has_no_number_and_computed_totals(draft):
    assert draft.number is None
    assert draft.total == D("1936.00")
    assert draft.lines.count() == 1
    assert AuditEvent.objects.filter(action="invoice.draft_create", object_id=str(draft.pk)).exists()


def test_preview_does_not_consume_number(profile, partner):
    """Scénář 5: náhled bez uložení, bez čísla, s označením."""
    doc = services.build_preview(profile, header_for(profile, partner), [
        line(rate="21"), line("Licence", qty="2", price="500", rate="12")])
    assert doc["number"] == "NÁVRH"
    assert "NENÍ VYSTAVENÝ DOKLAD" in doc["watermark"]
    assert doc["issuer"]["ico"] == profile.ico
    assert doc["bank"]["iban"] == profile.default_bank_account.iban
    assert {str(r.rate_percent.normalize()) for r in doc["vat_rows"]} == {"21", "12"}
    assert not Invoice.objects.exists()
    assert not NumberCounter.objects.filter(last_value__isnull=False).exists()


def test_preview_qr_uses_profile_account_without_vs(profile, partner):
    doc = services.build_preview(profile, header_for(profile, partner), [line()])
    spd = parse_spayd(doc["qr"]["payload"])
    assert spd["ACC"].startswith(profile.default_bank_account.iban)
    assert spd["AM"] == "1936.00"
    assert "X-VS" not in spd  # VS se přidělí až s číslem


def test_partner_of_other_profile_is_rejected(actor, profile, other_profile, make_partner):
    foreign = make_partner(other_profile, name="Cizí")
    with pytest.raises(ValidationError):
        services.create_draft(actor, profile, header_for(profile, foreign), [line()])


def test_bank_account_of_other_profile_is_rejected(actor, profile, other_profile, partner):
    head = header_for(profile, partner, bank_account=other_profile.default_bank_account)
    with pytest.raises(ValidationError):
        services.create_draft(actor, profile, head, [line()])


def test_update_draft_with_stale_version_conflicts(actor, draft, profile, partner):
    """Scénář 16: optimistický zámek."""
    services.update_draft(actor, draft, draft.version, header_for(profile, partner), [line(price="10")])
    with pytest.raises(ConcurrentUpdate):
        services.update_draft(actor, draft, draft.version, header_for(profile, partner), [line(price="20")])
    draft.refresh_from_db()
    assert draft.total == D("12.10")


def test_inactive_profile_cannot_create(actor, profile, partner):
    profile.is_active = False
    profile.save()
    with pytest.raises(ValidationError):
        services.create_draft(actor, profile, header_for(profile, partner), [line()])


# --- vystavení -------------------------------------------------------------------

def test_issue_assigns_number_vs_snapshots_and_pdf(actor, draft, profile):
    invoice = issue(actor, draft)
    assert invoice.status == "issued"
    assert invoice.number == "261"  # {YY}{N}, start 1, rok 2026
    assert invoice.variable_symbol == "261"
    assert invoice.issuer_snapshot["ico"] == profile.ico
    assert invoice.bank_snapshot["iban"] == profile.default_bank_account.iban
    assert invoice.partner_snapshot["name"] == "DvB-AF s.r.o."
    pdf = Path(settings.MEDIA_ROOT) / invoice.pdf_file
    assert pdf.read_bytes().startswith(b"%PDF")
    assert len(invoice.pdf_sha256) == 64 and len(invoice.data_hash) == 64
    assert invoice.issued_by == actor.user
    assert AuditEvent.objects.filter(action="invoice.issue", object_id=str(invoice.pk)).exists()


def test_numbers_follow_start_value_per_profile(actor, profile, other_profile, partner, make_partner):
    """Scénář 6: vlastní start řady v každém profilu; stejné číslo ve dvou profilech je povoleno."""
    NumberSeries.objects.filter(profile=profile).update(start_value=57)
    NumberSeries.objects.filter(profile=other_profile).update(start_value=57)
    other_partner = make_partner(other_profile)
    a = issue(actor, services.create_draft(actor, profile, header_for(profile, partner), [line()]))
    b = issue(actor, services.create_draft(actor, other_profile, header_for(other_profile, other_partner), [line()]))
    c = issue(actor, services.create_draft(actor, profile, header_for(profile, partner), [line()]))
    assert (a.number, b.number, c.number) == ("2657", "2657", "2658")


def test_yearly_series_restarts(actor, profile, partner):
    i1 = issue(actor, services.create_draft(actor, profile, header_for(profile, partner), [line()]))
    head = header_for(profile, partner, issue_date=date(2027, 1, 5), due_date=date(2027, 1, 19))
    i2 = issue(actor, services.create_draft(actor, profile, head, [line()]))
    assert (i1.number, i2.number) == ("261", "271")


def test_duplicate_number_in_series_is_impossible(actor, profile, partner):
    first = issue(actor, services.create_draft(actor, profile, header_for(profile, partner), [line()]))
    NumberCounter.objects.filter(series=first.series).update(last_value=0)  # simulace chyby
    second = services.create_draft(actor, profile, header_for(profile, partner), [line()])
    with pytest.raises(ValidationError, match="už existuje"):
        issue(actor, second)
    second.refresh_from_db()
    assert second.status == "draft" and second.number is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_issue_never_duplicates(make_user, make_profile, make_partner):
    """Scénář 7: dvě souběžná vystavení ve stejné řadě."""
    admin = make_user("admin@test.cz", admin=True)
    profile = make_profile("f13", "06646654")
    partner = make_partner(profile)
    actor = Actor(user=admin)
    drafts = [services.create_draft(actor, profile, header_for(profile, partner), [line()]) for _ in range(6)]
    barrier = threading.Barrier(len(drafts))
    errors = []

    def worker(inv):
        try:
            barrier.wait()
            services.issue(actor, inv, inv.version)
        except Exception as exc:  # pragma: no cover - chyba testu
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(d,)) for d in drafts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    numbers = sorted(Invoice.objects.values_list("number", flat=True))
    assert numbers == [f"26{i}" for i in range(1, 7)]


def test_issue_problems_block(actor, profile, partner):
    head = header_for(profile, partner)
    invoice = services.create_draft(actor, profile, head, [line(price="0")])
    with pytest.raises(ValidationError, match="kladná"):
        issue(actor, invoice)
    assert not NumberCounter.objects.filter(last_value__isnull=False).exists()


def test_pdf_failure_rolls_back_everything(actor, draft, monkeypatch):
    def boom(doc):
        raise RuntimeError("PDF selhalo")

    monkeypatch.setattr("invoicing.pdf.render_pdf", boom)
    with pytest.raises(RuntimeError):
        issue(actor, draft)
    draft.refresh_from_db()
    assert draft.status == "draft" and draft.number is None
    assert not NumberCounter.objects.filter(last_value__isnull=False).exists()
    assert not list(Path(settings.MEDIA_ROOT).rglob("*.pdf"))


def test_manual_vs_rule(actor, profile, partner):
    profile.vs_rule = "manual"
    profile.save()
    invoice = services.create_draft(actor, profile, header_for(profile, partner, variable_symbol="777"), [line()])
    assert issue(actor, invoice).variable_symbol == "777"


def test_constant_symbol_from_profile(actor, profile, partner):
    profile.constant_symbol = "0308"
    profile.save()
    invoice = issue(actor, services.create_draft(actor, profile, header_for(profile, partner), [line()]))
    assert invoice.constant_symbol == "0308"


# --- neměnnost (scénáře 9, 10, 20) ---------------------------------------------

def test_profile_and_partner_changes_do_not_touch_issued(actor, draft, profile, partner):
    invoice = issue(actor, draft)
    sha = invoice.pdf_sha256
    profile.name = "Jiné jméno"
    profile.save()
    partner.street = "Nová 1"
    partner.save()
    acc = profile.default_bank_account
    acc.account_number = "999/0100"
    acc.save()
    invoice.refresh_from_db()
    assert invoice.issuer_snapshot["name"] == "F13 CyberTech s.r.o."
    assert invoice.partner_snapshot["street"] == "Meleček 227"
    assert invoice.bank_snapshot["account_number"] == "2201090240/6210"
    assert invoice.pdf_sha256 == sha


def test_db_refuses_rewriting_issued_invoice(actor, draft, other_profile):
    invoice = issue(actor, draft)
    for change in ({"total": D("1")}, {"profile_id": other_profile.pk}, {"number": "999"},
                   {"status": "draft"}):
        with pytest.raises(IntegrityError), transaction.atomic():
            Invoice.objects.filter(pk=invoice.pk).update(**change)
    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.filter(pk=invoice.pk).delete()
    with pytest.raises(IntegrityError), transaction.atomic():
        InvoiceLine.objects.filter(invoice=invoice).update(unit_price=D("1"))
    with pytest.raises(IntegrityError), transaction.atomic():
        InvoiceLine.objects.create(invoice=invoice, description="x", unit_price=D("1"))


def test_issued_invoice_cannot_be_edited_via_service(actor, draft, profile, partner):
    invoice = issue(actor, draft)
    with pytest.raises(ValidationError):
        services.update_draft(actor, invoice, invoice.version, header_for(profile, partner), [line()])
    with pytest.raises(ValidationError):
        services.delete_draft(actor, invoice, invoice.version)


def test_draft_can_be_deleted(actor, draft):
    services.delete_draft(actor, draft, draft.version)
    assert not Invoice.objects.exists()


# --- storno ------------------------------------------------------------------------

def test_cancel_requires_reason_and_no_payment(actor, draft):
    invoice = issue(actor, draft)
    with pytest.raises(ValidationError):
        services.cancel(actor, invoice, invoice.version, " ")
    services.add_payment(actor, invoice, amount=D("100"), paid_on=date(2026, 10, 20), method="bank_transfer")
    with pytest.raises(ValidationError, match="úhradou"):
        services.cancel(actor, invoice, invoice.version, "chyba")


def test_cancel_keeps_number(actor, draft, profile, partner):
    invoice = issue(actor, draft)
    cancelled = services.cancel(actor, invoice, invoice.version, "Chybný odběratel")
    assert cancelled.status == "cancelled" and cancelled.number == "261"
    nxt = issue(actor, services.create_draft(actor, profile, header_for(profile, partner), [line()]))
    assert nxt.number == "262"
    assert AuditEvent.objects.filter(action="invoice.cancel", note="Chybný odběratel").exists()


# --- platby a stavy (scénáře 13, 14) ----------------------------------------------

def state(invoice, today=date(2026, 10, 20)):
    return Invoice.objects.with_payment_state(today).get(pk=invoice.pk)


def test_partial_full_over_payment_states(actor, draft):
    invoice = issue(actor, draft)
    assert state(invoice).payment_state == "unpaid"
    services.add_payment(actor, invoice, amount=D("936"), paid_on=date(2026, 10, 18), method="bank_transfer")
    s = state(invoice)
    assert (s.payment_state, s.remaining_amount) == ("partial", D("1000.00"))
    services.add_payment(actor, invoice, amount=D("1000"), paid_on=date(2026, 10, 19), method="bank_transfer")
    assert state(invoice).payment_state == "paid"
    services.add_payment(actor, invoice, amount=D("5"), paid_on=date(2026, 10, 19), method="cash")
    s = state(invoice)
    assert (s.payment_state, s.remaining_amount) == ("overpaid", D("-5.00"))


def test_overdue_is_derived_from_due_date(actor, draft):
    invoice = issue(actor, draft)
    assert not state(invoice, date(2026, 10, 25)).is_overdue
    assert state(invoice, date(2026, 10, 26)).is_overdue
    services.add_payment(actor, invoice, amount=D("1936"), paid_on=date(2026, 10, 26), method="bank_transfer")
    assert not state(invoice, date(2026, 10, 27)).is_overdue


def test_payment_reversal_is_counter_entry(actor, draft):
    invoice = issue(actor, draft)
    payment = services.add_payment(actor, invoice, amount=D("500"), paid_on=date(2026, 10, 20), method="cash")
    reversal = services.reverse_payment(actor, payment)
    assert reversal.amount == D("-500.00") and reversal.reverses == payment
    assert state(invoice).payment_state == "unpaid"
    with pytest.raises(ValidationError):
        services.reverse_payment(actor, payment)
    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.filter(pk=payment.pk).update(amount=D("1"))


def test_payment_only_on_issued_and_positive(actor, draft):
    with pytest.raises(ValidationError):
        services.add_payment(actor, draft, amount=D("1"), paid_on=date(2026, 10, 1), method="cash")
    invoice = issue(actor, draft)
    with pytest.raises(ValidationError):
        services.add_payment(actor, invoice, amount=D("0"), paid_on=date(2026, 10, 1), method="cash")


def test_delivery_and_reminders(actor, draft):
    invoice = issue(actor, draft)
    services.mark_delivered(actor, invoice, delivered_on=date(2026, 10, 16), note="e-mailem")
    invoice.refresh_from_db()
    assert invoice.delivery_status == "delivered_outside"
    assert invoice.status == "issued" and invoice.number == "261"  # nový doklad nevznikl
    due = services.reminders_due(invoice.profile, today=invoice.due_date + timedelta(days=8))
    assert [d["rule"].days_after_due for d in due] == [7]
    services.record_reminder(actor, invoice, reminded_on=date(2026, 11, 2), method="telefon")
    assert services.reminders_due(invoice.profile, today=invoice.due_date + timedelta(days=8)) == []
    due = services.reminders_due(invoice.profile, today=invoice.due_date + timedelta(days=22))
    assert [d["rule"].days_after_due for d in due] == [21]


# --- oprávnění ve službách --------------------------------------------------------

def test_services_enforce_permissions(make_user, draft, profile, partner):
    viewer = make_user("viewer@test.cz", profiles={profile: {"invoices:view"}})
    actor = Actor(user=viewer)
    with pytest.raises(AccessDenied):
        services.create_draft(actor, profile, header_for(profile, partner), [line()])
    with pytest.raises(AccessDenied):
        services.issue(actor, draft, draft.version)
