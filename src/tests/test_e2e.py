"""E2E v prohlížeči (Playwright + live_server): hlavní tok uživatele."""

import re

import pytest

from conftest import make_token

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def browser_for(browser, live_server):
    contexts = []

    def _open(email):
        context = browser.new_context(
            base_url=live_server.url,
            extra_http_headers={"Cf-Access-Jwt-Assertion": make_token(email)},
            locale="cs-CZ",
        )
        contexts.append(context)
        return context.new_page()

    yield _open
    for c in contexts:
        c.close()


def test_invoice_lifecycle_in_browser(browser_for, make_user, make_profile, make_partner):
    profile = make_profile("f13", "06646654", name="F13 CyberTech s.r.o.")
    other = make_profile("martin", "02963281", name="Martin Piech", account="1234567899", bank="0100")
    make_partner(profile, name="DvB-AF s.r.o.")
    make_user("admin@test.cz", admin=True)
    page = browser_for("admin@test.cz")

    page.goto("/")
    page.get_by_role("link", name=re.compile("F13 CyberTech")).first.click()
    assert "F13 CyberTech s.r.o." in page.get_by_test_id("active-profile").inner_text()
    assert page.get_by_text("TESTOVACÍ PROSTŘEDÍ").is_visible()

    # nová faktura se dvěma položkami a živými součty
    page.get_by_role("link", name="Nová faktura").first.click()
    page.select_option("#id_partner", label="DvB-AF s.r.o.")
    page.fill("#id_lines-0-description", "Servisní práce")
    page.fill("#id_lines-0-unit_price", "1600")
    page.get_by_role("button", name="+ Přidat položku").click()
    page.fill("#id_lines-1-description", "Licence")
    page.fill("#id_lines-1-quantity", "2")
    page.fill("#id_lines-1-unit_price", "500")
    page.select_option("#id_lines-1-vat_rate_percent", label="12 %")
    page.locator("#id_lines-1-unit_price").press("Tab")
    total = page.get_by_test_id("total")
    total.wait_for()
    page.wait_for_function(
        "document.querySelector('[data-testid=total]').innerText.replace(/\\s/g, ' ').includes('3 056,00')"
    )
    assert "3 056,00" in total.inner_text().replace("\xa0", " ")

    page.get_by_role("button", name="Uložit návrh").click()
    page.wait_for_url(re.compile(r"/faktury/\d+/$"))
    assert page.get_by_text("Návrh faktury uložen").is_visible()

    # potvrzení s rekapitulací vystavitele (scénář 4) a vystavení
    page.get_by_test_id("issue-link").click()
    recap = page.get_by_test_id("issue-recap").inner_text()
    for expected in ("F13 CyberTech s.r.o.", "06646654", profile.default_bank_account.iban, "261"):
        assert expected in recap
    page.get_by_test_id("confirm-issue").click()
    page.wait_for_url(re.compile(r"/faktury/\d+/$"))
    assert page.get_by_role("heading", name=re.compile("Faktura 261")).is_visible()

    # PDF ke stažení
    with page.expect_download() as download:
        page.get_by_test_id("download-pdf").click()
    assert download.value.suggested_filename == "faktura-261.pdf"

    # částečná platba → zbývá
    page.fill("#id_amount", "1000")
    page.get_by_test_id("add-payment").click()
    page.wait_for_url(re.compile(r"/faktury/\d+/$"))
    assert "2 056,00" in page.get_by_test_id("remaining").inner_text().replace("\xa0", " ")
    assert page.get_by_text("částečně uhrazeno").first.is_visible()

    # souhrn profilu
    page.get_by_role("link", name="Přehled").click()
    assert "3 056,00" in page.get_by_test_id("summary").inner_text().replace("\xa0", " ")

    # jiný profil faktury F13 nezobrazí
    page.goto(f"/p/{other.slug}/faktury/")
    assert "Žádné faktury" in page.get_by_test_id("invoice-table").inner_text()


def test_user_without_access_sees_nothing(browser_for, make_user, make_profile):
    profile = make_profile("f13", "06646654")
    make_user("nikdo@test.cz")
    page = browser_for("nikdo@test.cz")
    page.goto("/")
    assert page.get_by_text("Nemáte přidělený žádný fakturační profil").is_visible()
    response = page.goto(f"/p/{profile.slug}/faktury/")
    assert response.status == 404


def test_no_horizontal_overflow_on_mobile(browser, live_server, make_user, make_profile, make_partner):
    """Na šířce telefonu (390 px) žádná stránka nepřetéká do strany; široké tabulky se posouvají samy."""
    from accounts.permissions import Actor
    from conftest import header_for, line
    from invoicing import services

    profile = make_profile("f13", "06646654", name="F13 CyberTech s.r.o.")
    partner = make_partner(profile, name="Velmi dlouhý název odběratele s.r.o. pro test zalamování")
    admin = make_user("admin@test.cz", admin=True)
    actor = Actor(user=admin)
    inv = services.create_draft(actor, profile, header_for(profile, partner),
                                [line("Servisní práce dle smlouvy SM201604 – správa počítačů a sítě")])
    inv = services.issue(actor, inv, inv.version)
    draft = services.create_draft(actor, profile, header_for(profile, partner), [line()])

    context = browser.new_context(
        base_url=live_server.url, viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
        extra_http_headers={"Cf-Access-Jwt-Assertion": make_token(admin.email)},
    )
    page = context.new_page()
    s = profile.slug
    paths = [
        "/", f"/p/{s}/", f"/p/{s}/faktury/", f"/p/{s}/faktury/nova/", f"/p/{s}/faktury/{inv.pk}/",
        f"/p/{s}/faktury/{draft.pk}/vystavit/", f"/p/{s}/faktury/{draft.pk}/upravit/", f"/p/{s}/partneri/",
        f"/p/{s}/partneri/{partner.pk}/", f"/p/{s}/nastaveni/", f"/p/{s}/nastaveni/upravit/",
        "/sprava/uzivatele/", f"/sprava/uzivatele/{admin.pk}/", "/sprava/profily/", "/sprava/audit/",
        "/sprava/souhrn/",
    ]
    for path in paths:
        page.goto(path)
        sw, w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert sw <= w, f"{path}: stránka je široká {sw} px při displeji {w} px"
        too_wide = page.evaluate(
            "[...document.querySelectorAll('table.data')].filter(t => t.scrollWidth > t.clientWidth + 1).length"
        )
        assert too_wide == 0, f"{path}: tabulka je širší než displej (posouvání do strany)"
    page.goto(f"/p/{s}/faktury/")
    row = page.locator("table.data tbody tr").first
    assert row.evaluate("r => getComputedStyle(r).display") == "block"  # řádek jako karta
    assert row.locator("td[data-label='Splatnost']").is_visible()
    context.close()
