from datetime import date
from decimal import Decimal as D

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from accounts.models import User
from accounts.permissions import Actor
from audit.models import AuditEvent
from conftest import client_for, header_for, line
from invoicing import services
from invoicing.models import Invoice, Payment
from partners.models import Partner

pytestmark = pytest.mark.django_db


def _profile_routes():
    """Všechny pojmenované URL s parametrem <slug> (data profilu)."""
    routes = []

    def walk(patterns, namespace=None):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, p.namespace or namespace)
            elif isinstance(p, URLPattern) and p.name and namespace:
                routes.append(f"{namespace}:{p.name}")

    walk(get_resolver().url_patterns)
    return [r for r in routes if r.split(":")[0] in {"invoicing", "partners", "profiles", "reports"}]


@pytest.fixture
def world(actor, profile, partner):
    issued = services.create_draft(actor, profile, header_for(profile, partner), [line()])
    issued = services.issue(actor, issued, issued.version)
    payment = services.add_payment(actor, issued, amount=D("10"), paid_on=date(2026, 10, 20),
                                   method="bank_transfer")
    draft = services.create_draft(actor, profile, header_for(profile, partner), [line()])
    return {"issued": issued, "draft": draft, "payment": payment, "partner": partner,
            "account": profile.default_bank_account, "series": profile.default_series}


def _kwargs(route, profile, world):
    ns, name = route.split(":")
    kw = {"slug": profile.slug}
    if ns == "invoicing" and name not in {"list", "create", "preview", "totals"}:
        kw["pk"] = world["draft"].pk if name in {"edit", "delete", "preview_draft", "issue"} else world["issued"].pk
        if name == "payment_reverse":
            kw["payment_pk"] = world["payment"].pk
    if ns == "partners" and name not in {"list", "create"}:
        kw["pk"] = world["partner"].pk
    if ns == "profiles":
        if name in {"item_create"}:
            kw["kind"] = "ucty"
        if name in {"item_edit", "item_delete"}:
            kw["kind"], kw["pk"] = "ucty", world["account"].pk
        if name == "series_shift":
            kw["pk"] = world["series"].pk
    return kw


def test_route_inventory_is_complete():
    routes = _profile_routes()
    assert "invoicing:issue" in routes and "partners:edit" in routes and "profiles:item_edit" in routes
    assert len(routes) >= 25


def test_outsider_gets_404_everywhere(world, profile, other_profile, make_user):
    """Scénář 3: bez přístupu k profilu nelze nic číst ani měnit (ani přímým požadavkem)."""
    outsider = make_user("out@test.cz", profiles={other_profile: {"invoices:view", "invoices:modify",
                                                                   "partners:modify"}})
    client = client_for(outsider)
    before = (Invoice.objects.count(), Payment.objects.count(), Partner.objects.count())
    for route in _profile_routes():
        url = reverse(route, kwargs=_kwargs(route, profile, world))
        for method in ("get", "post"):
            response = getattr(client, method)(url, {"version": 1, "reason": "x", "amount": "1"})
            assert response.status_code == 404, f"{method.upper()} {url} -> {response.status_code}"
            assert b"1936" not in response.content
    assert (Invoice.objects.count(), Payment.objects.count(), Partner.objects.count()) == before
    world["issued"].refresh_from_db()
    assert world["issued"].status == "issued"


def test_viewer_can_read_but_not_write(world, profile, make_user):
    viewer = make_user("v@test.cz", profiles={profile: {"invoices:view"}})
    c = client_for(viewer)
    assert c.get(reverse("invoicing:list", kwargs={"slug": profile.slug})).status_code == 200
    assert c.get(reverse("invoicing:detail", kwargs={"slug": profile.slug, "pk": world["issued"].pk})).status_code == 200
    assert c.get(reverse("invoicing:pdf", kwargs={"slug": profile.slug, "pk": world["issued"].pk})).status_code == 200
    for route in ["invoicing:create", "invoicing:issue", "invoicing:payment_add", "invoicing:cancel",
                  "partners:create", "partners:edit", "profiles:settings"]:
        url = reverse(route, kwargs=_kwargs(route, profile, world))
        assert c.post(url, {"version": 1}).status_code == 404, url
    assert Payment.objects.count() == 1


def test_partner_manager_without_invoice_rights(world, profile, make_user):
    pm = make_user("pm@test.cz", profiles={profile: {"partners:modify"}})
    c = client_for(pm)
    assert c.get(reverse("partners:list", kwargs={"slug": profile.slug})).status_code == 200
    assert c.get(reverse("invoicing:list", kwargs={"slug": profile.slug})).status_code == 404
    url = reverse("partners:edit", kwargs={"slug": profile.slug, "pk": world["partner"].pk})
    data = {"name": "Nový název", "country": "Česká republika", "is_customer": "on", "is_active": "on",
            "version": world["partner"].version}
    assert c.post(url, data).status_code == 302
    world["partner"].refresh_from_db()
    assert world["partner"].name == "Nový název"
    assert AuditEvent.objects.filter(action="partner.update").exists()


def test_non_admin_cannot_use_admin_area(make_user, profile):
    user = make_user("u@test.cz", profiles={profile: {"invoices:modify"}})
    c = client_for(user)
    for url in ["/sprava/uzivatele/", "/sprava/profily/novy/", "/sprava/audit/", "/sprava/souhrn/",
                reverse("profiles:settings", kwargs={"slug": profile.slug})]:
        assert c.get(url).status_code == 404, url


def test_admin_pages_smoke(world, admin, profile):
    c = client_for(admin)
    urls = ["/", "/sprava/uzivatele/", "/sprava/uzivatele/novy/", f"/sprava/uzivatele/{admin.pk}/",
            "/sprava/profily/", "/sprava/profily/novy/", "/sprava/audit/", "/sprava/souhrn/"]
    for route in _profile_routes():
        if route.split(":")[1] in {"preview", "totals", "delete", "cancel", "payment_add", "payment_reverse",
                                   "delivered", "reminder", "item_delete", "copy", "logo_file"}:
            continue
        urls.append(reverse(route, kwargs=_kwargs(route, profile, world)))
    for url in urls:
        response = c.get(url)
        assert response.status_code in (200, 302), f"{url} -> {response.status_code}"
    for route in ["invoicing:list", "partners:list"]:
        response = c.get(reverse(route, kwargs={"slug": profile.slug}) + "?format=csv")
        assert response["Content-Type"].startswith("text/csv")
        assert response.content.startswith("﻿".encode())


def _invoice_post(profile, partner, **extra):
    data = {
        "partner": partner.pk, "series": profile.default_series.pk, "vat_regime": profile.default_vat_regime.pk,
        "issue_date": "2026-10-15", "taxable_supply_date": "2026-09-30", "due_date": "2026-10-25",
        "payment_method": "bank_transfer", "bank_account": profile.default_bank_account.pk,
        "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "1", "lines-MAX_NUM_FORMS": "1000",
        "lines-0-description": "Servisní práce", "lines-0-quantity": "1", "lines-0-unit_price": "1600",
        "lines-0-discount_percent": "0", "lines-0-vat_rate_percent": "21.00",
        "lines-1-description": "Licence", "lines-1-quantity": "2", "lines-1-unit_price": "500",
        "lines-1-discount_percent": "0", "lines-1-vat_rate_percent": "12.00",
    }
    data.update(extra)
    return data


def test_full_web_flow(admin, profile, partner):
    """Scénáře 4, 5, 8, 20: návrh → náhled → potvrzení s rekapitulací → vystavení → PDF."""
    c = client_for(admin)
    slug = profile.slug
    preview = c.post(reverse("invoicing:preview", kwargs={"slug": slug}), _invoice_post(profile, partner))
    assert preview["Content-Type"] == "application/pdf" and preview.content.startswith(b"%PDF")
    assert not Invoice.objects.exists()

    totals = c.post(reverse("invoicing:totals", kwargs={"slug": slug}), _invoice_post(profile, partner))
    assert "3 056,00" in totals.content.decode().replace("\xa0", " ")

    response = c.post(reverse("invoicing:create", kwargs={"slug": slug}), _invoice_post(profile, partner))
    invoice = Invoice.objects.get()
    assert response.status_code == 302 and invoice.total == D("3056.00") and invoice.number is None

    confirm = c.get(reverse("invoicing:issue", kwargs={"slug": slug, "pk": invoice.pk}))
    text = confirm.content.decode()
    for expected in (profile.name, profile.ico, profile.default_bank_account.iban, "Faktury ({YY}{N})", "261"):
        assert expected in text

    c.post(reverse("invoicing:issue", kwargs={"slug": slug, "pk": invoice.pk}), {"version": invoice.version})
    invoice.refresh_from_db()
    assert invoice.status == "issued" and invoice.number == "261"

    pdf1 = c.get(reverse("invoicing:pdf", kwargs={"slug": slug, "pk": invoice.pk}))
    pdf2 = c.get(reverse("invoicing:pdf", kwargs={"slug": slug, "pk": invoice.pk}) + "?stahnout=1")
    assert b"".join(pdf1.streaming_content) == b"".join(pdf2.streaming_content)
    assert Invoice.objects.count() == 1


def test_form_validation_errors_are_shown(admin, profile, partner):
    c = client_for(admin)
    data = _invoice_post(profile, partner, **{"lines-0-unit_price": "abc", "due_date": "2026-01-01"})
    response = c.post(reverse("invoicing:create", kwargs={"slug": profile.slug}), data)
    assert response.status_code == 200
    assert not Invoice.objects.exists()


def test_payment_and_cancel_via_web(admin, world, profile):
    c = client_for(admin)
    inv = world["issued"]
    base = {"slug": profile.slug, "pk": inv.pk}
    c.post(reverse("invoicing:payment_add", kwargs=base),
           {"amount": "1926", "paid_on": "2026-10-21", "method": "bank_transfer"})
    assert Invoice.objects.with_payment_state().get(pk=inv.pk).payment_state == "paid"
    response = c.post(reverse("invoicing:cancel", kwargs=base), {"reason": "x", "version": inv.version}, follow=True)
    assert "nelze stornovat" in response.content.decode()


def test_summaries_stay_separate(actor, profile, other_profile, partner, make_partner, admin):
    """Scénář 15: obraty profilů se nesčítají."""
    p2 = make_partner(other_profile)
    for prof, part, price in ((profile, partner, "1000"), (other_profile, p2, "500")):
        inv = services.create_draft(actor, prof, header_for(prof, part), [line(price=price, rate="0")])
        services.issue(actor, inv, inv.version)
    c = client_for(admin)
    r1 = c.get(reverse("reports:dashboard", kwargs={"slug": profile.slug}) + "?rok=2026").content.decode()
    r2 = c.get(reverse("reports:dashboard", kwargs={"slug": other_profile.slug}) + "?rok=2026").content.decode()
    assert "1\xa0000,00" in r1 and "500,00" not in r1.split("Rozpracované")[0]
    assert "500,00" in r2 and "1\xa0500,00" not in r2
    g = c.get("/sprava/souhrn/?rok=2026").content.decode()
    assert "1\xa0500,00" not in g


def test_test_environment_banner(admin, settings):
    """Scénář 23: test je výrazně označen, produkce ne."""
    assert "TESTOVACÍ PROSTŘEDÍ" in client_for(admin).get("/").content.decode()
    settings.APP_ENVIRONMENT = "production"
    assert "TESTOVACÍ PROSTŘEDÍ" not in client_for(admin).get("/").content.decode()


def test_deactivated_profile_history_readable(world, profile, make_user):
    """Scénář 21."""
    user = make_user("u@test.cz", profiles={profile: {"invoices:modify"}})
    profile.is_active = False
    profile.save()
    c = client_for(user)
    assert c.get(reverse("invoicing:detail", kwargs={"slug": profile.slug, "pk": world["issued"].pk})).status_code == 200
    c.post(reverse("invoicing:create", kwargs={"slug": profile.slug}), _invoice_post(profile, world["partner"]))
    assert Invoice.objects.count() == 2  # jen původní dvě


def test_admin_creates_profile_and_assigns_user(admin, make_user):
    """Scénář 2."""
    c = client_for(admin)
    response = c.post("/sprava/profily/novy/", {
        "slug": "osvc", "name": "Petr OSVČ", "street": "Ulice 1", "city": "Budapešť", "zip_code": "1165",
        "country": "Maďarsko", "ico": "12345678", "dic": "", "vat_payer": "", "email": "", "phone": "",
        "legal_form": "fyzická osoba"})
    assert response.status_code == 302
    from profiles.models import BillingProfile
    p = BillingProfile.objects.get(slug="osvc")
    assert p.default_series and p.default_vat_regime.code == "neplatce"
    dup = c.post("/sprava/profily/novy/", {"slug": "osvc2", "name": "X", "street": "a", "city": "b",
                                            "zip_code": "1", "country": "c", "ico": "12345678"})
    assert dup.status_code == 200 and "IČO již existuje" in dup.content.decode()

    user = make_user("u@test.cz")
    response = c.post(f"/sprava/uzivatele/{user.pk}/", {
        "email": user.email, "name": "U", "is_active": "on", "version": user.version,
        f"p{p.pk}_can_modify_invoices": "on"})
    assert response.status_code == 302
    assert user.memberships.get(profile=p).can_modify_invoices
    assert AuditEvent.objects.filter(action="membership.change", profile=p).exists()
    stale = c.post(f"/sprava/uzivatele/{user.pk}/", {"email": user.email, "name": "Z", "version": user.version},
                   follow=True)
    assert "změnil jiný uživatel" in stale.content.decode()
    assert User.objects.get(pk=user.pk).name == "U"


def test_logo_upload_validation(admin, profile):
    c = client_for(admin)
    from django.core.files.uploadedfile import SimpleUploadedFile

    url = reverse("profiles:logo", kwargs={"slug": profile.slug})
    bad = SimpleUploadedFile("logo.png", b"<svg></svg>", content_type="image/png")
    assert "není platný obrázek" in c.post(url, {"logo": bad}).content.decode()
    png = SimpleUploadedFile("logo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 10, content_type="image/png")
    assert c.post(url, {"logo": png}).status_code == 302


def test_series_shift_only_upwards(admin, world, profile):
    c = client_for(admin)
    url = reverse("profiles:series_shift", kwargs={"slug": profile.slug, "pk": profile.default_series.pk})
    r = c.post(url, {"year": 2026, "next_value": 1, "reason": "x"})
    assert "lze jen zvýšit" in r.content.decode()
    assert c.post(url, {"year": 2026, "next_value": 50, "reason": "navázání na Premier"}).status_code == 302
    inv = services.create_draft(Actor(user=admin), profile, header_for(profile, world["partner"]), [line()])
    assert services.issue(Actor(user=admin), inv, inv.version).number == "2650"


def test_first_bank_account_becomes_default(admin, make_profile):
    from profiles.models import BankAccount, BillingProfile

    p = BillingProfile.objects.create(slug="novy", name="Nový", street="a", city="b", zip_code="1",
                                      ico="11111111")
    c = client_for(admin)
    c.post(reverse("profiles:item_create", kwargs={"slug": p.slug, "kind": "ucty"}), {
        "label": "Hlavní", "account_number": "2201090240/6210", "iban": "CZ0762106701002201090240",
        "bic": "BREXCZPP", "bank_name": "mBank", "is_active": "on"})
    p.refresh_from_db()
    assert p.default_bank_account == BankAccount.objects.get(profile=p)


def test_bootstrap_fills_missing_default_account(make_profile, settings):
    from django.core.management import call_command

    from profiles.models import BillingProfile

    p = make_profile("x", "22222222")
    BillingProfile.objects.filter(pk=p.pk).update(default_bank_account=None)
    settings.BOOTSTRAP_ADMIN_EMAILS = []
    call_command("bootstrap")
    p.refresh_from_db()
    assert p.default_bank_account is not None
