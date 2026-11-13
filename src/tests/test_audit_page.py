"""Stránka auditu: filtry, čitelný souhrn změny a rozbalitelný detail."""

import json
import re
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from accounts.permissions import Actor
from audit import services as audit
from audit.models import AuditEvent
from audit.presentation import action_label, present
from conftest import client_for, header_for, line
from invoicing import services as inv

PRAGUE = ZoneInfo("Europe/Prague")
URL = "/sprava/audit/"


def _at(*args):
    return mock.patch("django.utils.timezone.now", return_value=datetime(*args, tzinfo=PRAGUE))


def _rows(html):
    return re.findall(r'<tr class="audit-row" data-audit-id="(\d+)"', html)


@pytest.fixture
def events(admin, make_user, profile, partner):
    other = make_user("ucetni@test.cz", admin=True)
    with _at(2026, 11, 1, 9, 0):
        e1 = audit.record(Actor(user=admin), "partner.update", profile=profile, obj=partner,
                          before={"name": "Stará", "version": 1}, after={"name": "Nová", "version": 2})
    with _at(2026, 11, 5, 12, 0):
        e2 = audit.record(Actor(user=other, interface="mcp"), "mcp.call", profile=profile,
                          after={"tool": "search_invoices", "ok": True, "error": None},
                          note=json.dumps({"status": "issued", "client_request_id": "x"}))
    with _at(2026, 11, 10, 18, 30):
        e3 = audit.record(Actor(user=None, interface="system"), "user.bootstrap_admin", obj=admin,
                          after={"email": "admin@test.cz", "name": "admin", "is_admin": True})
    return {"admin": admin, "other": other, "e1": e1, "e2": e2, "e3": e3}


def _ids(admin, **params):
    resp = client_for(admin).get(URL, params)
    assert resp.status_code == 200
    return {int(i) for i in _rows(resp.content.decode())}


def test_filters(events, profile):
    a, e1, e2, e3 = events["admin"], events["e1"].pk, events["e2"].pk, events["e3"].pk
    assert {e1, e2, e3} <= _ids(a)
    assert _ids(a, rozhrani="mcp") == {e2}
    assert _ids(a, uzivatel=events["other"].pk) == {e2}
    assert _ids(a, uzivatel="system") == {e3}
    assert _ids(a, od="2026-11-02T00:00", do="2026-11-09T23:59") == {e2}
    assert _ids(a, od="2026-11-10T18:30") >= {e3} and e2 not in _ids(a, od="2026-11-10T18:30")
    assert _ids(a, akce="partner.") == {e1}
    assert _ids(a, akce="mcp.call") == {e2}
    assert _ids(a, profil="none") == {e3}
    assert _ids(a, profil=profile.pk) == {e1, e2}
    assert _ids(a, hledat="search_invoices") == set()  # hledá v poznámce, ne v nástroji
    assert _ids(a, hledat="client_request_id") == {e2}


def test_invalid_range_shows_error(events):
    resp = client_for(events["admin"]).get(URL, {"od": "2026-11-10T00:00", "do": "2026-11-01T00:00"})
    assert resp.status_code == 200
    assert "Konec musí být po začátku." in resp.content.decode()


def test_one_row_per_event_with_readable_summary_and_hidden_detail(events):
    html = client_for(events["admin"]).get(URL).content.decode()
    assert html.count('<tr class="audit-row"') == html.count('<tr class="audit-detail" hidden>')
    assert "název: Stará → Nová" in html
    changes = re.findall(r'<td class="change" title="([^"]*)"', html)
    assert "název: Stará → Nová" in changes and not any("verze" in c or "version" in c for c in changes)
    assert "nástroj: search_invoices · výsledek: OK · parametry: status=issued" in html
    assert "Upraven partner" in html and "Volání MCP nástroje" in html
    assert "Technický záznam (JSON)" in html
    assert "&quot;version&quot;: 2" in html  # surová data jen v rozbalitelném detailu


def test_pagination_keeps_filters(events, admin):
    for i in range(101):
        audit.record(Actor(user=admin, interface="mcp"), "mcp.call",
                     after={"tool": "list_profiles", "ok": True, "error": None}, note=str(i))
    html = client_for(admin).get(URL, {"rozhrani": "mcp"}).content.decode()
    assert "rozhrani=mcp" in re.search(r'href="([^"]*strana=2[^"]*)"', html).group(1)


def test_present_invoice_label_and_update_diff(actor, profile, partner):
    draft = inv.create_draft(actor, profile, header_for(profile, partner), [line()])
    inv.update_draft(actor, draft, draft.version,
                     header_for(profile, partner, order_reference="OBJ-7"), [line(price="2000.00")])
    events = AuditEvent.objects.filter(object_type="invoicing.invoice", object_id=str(draft.pk))
    views = {v.event.action: v for v in present(events)}
    upd = views["invoice.draft_update"]
    assert upd.object_label == f"Faktura návrh #{draft.pk}"
    assert upd.object_url.endswith(f"/faktury/{draft.pk}/")
    assert "objednávka: — → OBJ-7" in upd.summary_text
    assert "celkem: 1 936,00 → 2 420,00" in upd.summary_text
    assert "version" not in upd.summary_text and "verze" not in upd.summary_text
    create = views["invoice.draft_create"]
    assert f"odběratel: {partner.name}" in create.summary_text and "celkem: 1 936,00" in create.summary_text


def test_action_label_fallback():
    assert action_label("bankaccount.create") == "Založeno (bankaccount)"
    assert action_label("invoice.issue") == "Vystavena faktura"
