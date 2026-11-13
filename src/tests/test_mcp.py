"""MCP server: protokol, oprávnění, scénáře 17–19, idempotence, audit, ARES."""

import base64
import json
import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from accounts.permissions import Actor
from audit.models import AuditEvent
from conftest import client_for, header_for, line, make_token
from invoicing import services
from invoicing.models import Invoice, Payment
from mcpapi.models import IdempotencyRecord
from partners.models import Partner

pytestmark = pytest.mark.django_db

ARES_F13 = {
    "ico": "06646654", "obchodniJmeno": "F13 CyberTech s.r.o.", "dic": "CZ06646654",
    "sidlo": {"nazevStatu": "Česká republika", "nazevObce": "Ostrava", "nazevCastiObce": "Hulváky",
              "nazevUlice": "Sokola Tůmy", "cisloDomovni": 1099, "cisloOrientacni": 1, "psc": 70900,
              "textovaAdresa": "Sokola Tůmy 1099/1, Hulváky, 70900 Ostrava"},
}


class Mcp:
    def __init__(self, user):
        self.client = client_for(user)
        self.n = 0

    def rpc(self, method, params=None, **headers):
        self.n += 1
        body = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            body["params"] = params
        response = self.client.post("/mcp/", json.dumps(body), content_type="application/json", headers=headers)
        assert response.status_code == 200, response.content
        return response.json()

    def call(self, _tool, **arguments):
        data = self.rpc("tools/call", {"name": _tool, "arguments": arguments})
        assert "result" in data, data
        result = data["result"]
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        return result


def ok(result):
    assert result["isError"] is False, result["structuredContent"]
    return result["structuredContent"]


def err(result, code):
    assert result["isError"] is True, result["structuredContent"]
    assert result["structuredContent"]["error"]["code"] == code, result["structuredContent"]
    return result["structuredContent"]["error"]


def rid():
    return str(uuid.uuid4())


@pytest.fixture
def mcp(admin):
    return Mcp(admin)


@pytest.fixture
def issued(actor, profile, partner):
    inv = services.create_draft(actor, profile, header_for(profile, partner), [line()])
    return services.issue(actor, inv, inv.version)


# --- protokol -------------------------------------------------------------------------

def test_initialize_and_tools_list(mcp):
    init = mcp.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "test", "version": "1"}})["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["serverInfo"]["name"] == "faktury" and "tools" in init["capabilities"]
    assert "NÁVRH" in init["instructions"]
    assert mcp.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-11-25"
    tools = {t["name"]: t for t in mcp.rpc("tools/list")["result"]["tools"]}
    assert {"list_profiles", "search_partners", "find_last_invoice", "create_invoice_draft", "issue_invoice",
            "cancel_invoice", "record_payment", "create_partner", "lookup_company_ares"} <= set(tools)
    for t in tools.values():
        assert t["inputSchema"]["type"] == "object" and t["description"]
    assert tools["issue_invoice"]["annotations"]["destructiveHint"] is True
    assert tools["search_invoices"]["annotations"]["readOnlyHint"] is True
    assert "client_request_id" in tools["record_payment"]["inputSchema"]["required"]
    assert mcp.rpc("ping")["result"] == {}


def test_protocol_edge_cases(admin, mcp):
    c = client_for(admin)
    note = c.post("/mcp/", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                  content_type="application/json")
    assert note.status_code == 202
    assert c.get("/mcp/").status_code == 405
    assert c.post("/mcp/", "x=1", content_type="application/x-www-form-urlencoded").status_code == 415
    assert c.post("/mcp/", "{", content_type="application/json").json()["error"]["code"] == -32700
    assert c.post("/mcp/", "[]", content_type="application/json").status_code == 400
    evil = c.post("/mcp/", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                  content_type="application/json", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    bad_version = c.post("/mcp/", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                         content_type="application/json", headers={"MCP-Protocol-Version": "1999-01-01"})
    assert bad_version.status_code == 400
    assert mcp.rpc("nope")["error"]["code"] == -32601
    assert mcp.rpc("tools/call", {"name": "nope", "arguments": {}})["error"]["code"] == -32602
    assert c.post("/mcp", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                  content_type="application/json").status_code == 200


def test_mcp_requires_cloudflare_identity(admin):
    from django.test import Client

    response = Client().post("/mcp/", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                             content_type="application/json")
    assert response.status_code == 403


def test_invalid_arguments_are_structured(mcp, profile):
    e = err(mcp.call("get_invoice", profile=profile.slug, invoice_id="x"), "invalid_arguments")
    assert "invoice_id" in e["message"]
    err(mcp.call("search_invoices", unknown_field=1), "invalid_arguments")


# --- čtení ----------------------------------------------------------------------------------

def test_list_profiles_shows_only_accessible(make_user, profile, other_profile):
    user = make_user("u@test.cz", profiles={profile: {"invoices:view"}})
    data = ok(Mcp(user).call("list_profiles"))
    assert [p["slug"] for p in data["profiles"]] == [profile.slug]
    assert data["profiles"][0]["permissions"] == ["invoices:view", "partners:view"]


def test_every_result_names_the_issuer(mcp, issued, profile):
    data = ok(mcp.call("get_invoice", profile=profile.ico, invoice_id=issued.pk))
    inv = data["invoice"]
    assert inv["profile"] == {"id": profile.pk, "slug": profile.slug, "name": profile.name, "ico": profile.ico}
    assert inv["status"] == "issued" and inv["is_draft"] is False and inv["number"] == "261"
    assert inv["remaining"] == "1936.00" and inv["payment_state"] == "unpaid"
    assert inv["pdf_url"].endswith(f"/p/{profile.slug}/faktury/{issued.pk}/faktura.pdf")
    assert inv["lines"][0]["vat_rate_percent"] == "21"


def test_search_invoices_groups_by_profile(actor, mcp, issued, other_profile, make_partner):
    p2 = make_partner(other_profile)
    services.create_draft(actor, other_profile, header_for(other_profile, p2), [line()])
    data = ok(mcp.call("search_invoices", query="DvB"))
    groups = {g["profile"]["slug"]: g["invoices"] for g in data["results_by_profile"]}
    assert set(groups) == {"f13", "martin"}
    assert groups["martin"][0]["is_draft"] is True and groups["martin"][0]["number"] is None
    overdue = ok(mcp.call("search_invoices", overdue_only=True, profile="f13"))
    assert overdue["returned"] == 1  # splatnost 25. 10. 2026 je v minulosti


def test_pagination(actor, mcp, profile, partner):
    for _ in range(5):
        services.create_draft(actor, profile, header_for(profile, partner), [line()])
    first = ok(mcp.call("search_invoices", profile="f13", limit=2))
    assert first["returned"] == 2 and first["next_cursor"] == "2"
    last = ok(mcp.call("search_invoices", profile="f13", limit=2, cursor="4"))
    assert last["returned"] == 1 and last["next_cursor"] is None


def test_scenario_18_last_invoice_and_ambiguity(actor, mcp, issued, profile, other_profile, make_partner):
    """Scénář 18: poslední faktura partnera s profilem, stavem a zdrojem/časem platby; u partnera ve více
    profilech vyžádá upřesnění."""
    services.add_payment(actor, issued, amount=D("1000"), paid_on=date(2026, 10, 20), method="bank_transfer")
    data = ok(mcp.call("find_last_invoice", partner="DvB-AF"))
    assert data["profile"]["slug"] == "f13"
    inv = data["invoice"]
    assert (inv["number"], inv["issue_date"], inv["total"], inv["remaining"], inv["due_date"]) == (
        "261", "2026-10-15", "1936.00", "936.00", "2026-10-25")
    assert inv["payment_state"] == "partial"
    assert inv["last_payment"]["source"] == "manual" and inv["last_payment"]["recorded_at"]
    make_partner(other_profile)  # stejný partner i v druhém profilu
    e = err(mcp.call("find_last_invoice", partner="DvB-AF"), "ambiguous")
    assert {c["profile"]["slug"] for c in e["candidates"]} == {"f13", "martin"}
    assert ok(mcp.call("find_last_invoice", partner="DvB-AF", profile="f13"))["invoice"]["number"] == "261"


def test_summary_per_profile(mcp, issued, profile):
    data = ok(mcp.call("get_profile_summary", profile="f13"))
    assert data["profile"]["slug"] == "f13" and data["issued"] == "1936.00" and data["remaining"] == "1936.00"


# --- scénář 17: profil u zápisu ------------------------------------------------------------

def test_scenario_17_write_without_profile_asks(mcp, profile, other_profile, partner):
    e = err(mcp.call("create_invoice_draft", partner_id=partner.pk, client_request_id=rid(),
                     lines=[{"description": "X", "unit_price": "100"}]), "profile_required")
    assert {p["slug"] for p in e["available_profiles"]} == {"f13", "martin"}
    assert not Invoice.objects.exists()


def test_profile_is_never_guessed(mcp, profile, partner):
    e = err(mcp.call("create_invoice_draft", profile="F13 Cyber", partner_id=partner.pk, client_request_id=rid(),
                     lines=[{"description": "X", "unit_price": "100"}]), "not_found")
    assert e["available_profiles"]


def test_partner_from_other_profile_rejected(mcp, profile, other_profile, make_partner):
    foreign = make_partner(other_profile, name="Cizí")
    err(mcp.call("create_invoice_draft", profile="f13", partner_id=foreign.pk, client_request_id=rid(),
                 lines=[{"description": "X", "unit_price": "100"}]), "not_found")


# --- scénář 19 a zápisy ------------------------------------------------------------------------

def test_scenario_19_create_draft_idempotent(mcp, profile, partner):
    args = {"profile": "f13", "partner_id": partner.pk, "client_request_id": rid(), "due_days": 10,
            "lines": [{"description": "Servis", "unit_price": "1600", "vat_rate_percent": 21},
                      {"description": "Licence", "quantity": 2, "unit_price": "500", "vat_rate_percent": "12"}]}
    first = ok(mcp.call("create_invoice_draft", **args))
    assert first["invoice"]["is_draft"] is True and first["invoice"]["number"] is None
    assert first["invoice"]["total"] == "3056.00"
    assert first["issuer_ico"] == profile.ico and first["bank_account"]["iban"] == profile.default_bank_account.iban
    assert "NÁVRH" in first["message"]
    again = ok(mcp.call("create_invoice_draft", **args))
    assert again["idempotent_replay"] is True and again["invoice"]["id"] == first["invoice"]["id"]
    assert Invoice.objects.count() == 1 and Invoice.objects.get().status == "draft"
    changed = {**args, "due_days": 30}
    err(mcp.call("create_invoice_draft", **changed), "idempotency_conflict")
    assert Invoice.objects.count() == 1


def test_draft_validation(mcp, profile, partner):
    base = {"profile": "f13", "partner_id": partner.pk}
    e = err(mcp.call("create_invoice_draft", client_request_id=rid(), **base,
                     lines=[{"description": "X", "unit_price": "100", "vat_rate_percent": 15}]), "validation_error")
    assert "15" in e["message"]
    err(mcp.call("create_invoice_draft", client_request_id="short", **base,
                 lines=[{"description": "X", "unit_price": "100"}]), "invalid_arguments")
    err(mcp.call("create_invoice_draft", client_request_id=rid(), variable_symbol="123", **base,
                 lines=[{"description": "X", "unit_price": "100"}]), "invalid_arguments")
    assert not IdempotencyRecord.objects.exists()


def test_update_draft_and_version_conflict(mcp, profile, partner):
    created = ok(mcp.call("create_invoice_draft", profile="f13", partner_id=partner.pk, client_request_id=rid(),
                          lines=[{"description": "X", "unit_price": "100"}]))["invoice"]
    upd = ok(mcp.call("update_invoice_draft", profile="f13", invoice_id=created["id"], client_request_id=rid(),
                      expected_version=created["version"], lines=[{"description": "Y", "unit_price": "200"}]))
    assert upd["invoice"]["total"] == "242.00" and upd["invoice"]["lines"][0]["description"] == "Y"
    err(mcp.call("update_invoice_draft", profile="f13", invoice_id=created["id"], client_request_id=rid(),
                 expected_version=created["version"], public_note="z"), "version_conflict")


def test_preview_with_pdf(mcp, profile, partner):
    created = ok(mcp.call("create_invoice_draft", profile="f13", partner_id=partner.pk, client_request_id=rid(),
                          lines=[{"description": "X", "unit_price": "100"}]))["invoice"]
    result = mcp.call("preview_invoice_draft", profile="f13", invoice_id=created["id"], include_pdf=True)
    assert result["isError"] is False
    blob = result["content"][1]["resource"]["blob"]
    assert base64.b64decode(blob).startswith(b"%PDF")
    assert result["structuredContent"]["qr_payment"].startswith("SPD*1.0*")
    assert Invoice.objects.get().number is None


def test_issue_requires_confirmation(mcp, profile, partner):
    created = ok(mcp.call("create_invoice_draft", profile="f13", partner_id=partner.pk, client_request_id=rid(),
                          lines=[{"description": "X", "unit_price": "100"}]))["invoice"]
    e = err(mcp.call("issue_invoice", profile="f13", invoice_id=created["id"], expected_version=created["version"],
                     client_request_id=rid()), "confirmation_required")
    assert e["recap"]["issuer_ico"] == profile.ico and e["recap"]["expected_number"] == "261"
    assert Invoice.objects.get().status == "draft"
    issued = ok(mcp.call("issue_invoice", profile="f13", invoice_id=created["id"], confirm=True,
                         expected_version=created["version"], client_request_id=rid()))
    assert issued["invoice"]["number"] == "261" and issued["invoice"]["status"] == "issued"
    ev = AuditEvent.objects.get(action="invoice.issue")
    assert ev.interface == "mcp"


def test_cancel_requires_confirmation(mcp, issued, profile):
    err(mcp.call("cancel_invoice", profile="f13", invoice_id=issued.pk, expected_version=issued.version,
                 reason="Chybný odběratel", client_request_id=rid()), "confirmation_required")
    done = ok(mcp.call("cancel_invoice", profile="f13", invoice_id=issued.pk, expected_version=issued.version,
                       reason="Chybný odběratel", confirm=True, client_request_id=rid()))
    assert done["invoice"]["status"] == "cancelled"


def test_record_payment_idempotent(mcp, issued, profile):
    args = {"profile": "f13", "invoice_id": issued.pk, "amount": 1936, "paid_on": "2026-10-20",
            "client_request_id": rid()}
    first = ok(mcp.call("record_payment", **args))
    assert first["invoice"]["payment_state"] == "paid" and first["invoice"]["remaining"] == "0.00"
    ok(mcp.call("record_payment", **args))
    assert Payment.objects.count() == 1
    assert AuditEvent.objects.filter(action="payment.create", interface="mcp").count() == 1


def test_permissions_through_mcp(make_user, issued, profile, other_profile, partner):
    viewer = Mcp(make_user("v@test.cz", profiles={profile: {"invoices:view"}}))
    assert ok(viewer.call("get_invoice", profile="f13", invoice_id=issued.pk))["invoice"]["number"] == "261"
    err(viewer.call("record_payment", profile="f13", invoice_id=issued.pk, amount=1, paid_on="2026-10-20",
                    client_request_id=rid()), "forbidden")
    err(viewer.call("create_partner", profile="f13", name="X", use_ares=False, client_request_id=rid()),
        "forbidden")
    outsider = Mcp(make_user("o@test.cz", profiles={other_profile: {"invoices:modify"}}))
    err(outsider.call("get_invoice", profile="f13", invoice_id=issued.pk), "not_found")
    err(outsider.call("get_invoice", profile="martin", invoice_id=issued.pk), "not_found")
    data = ok(outsider.call("search_invoices", query="261"))
    assert data["returned"] == 0
    e = err(outsider.call("find_last_invoice", partner=partner.name), "not_found")
    assert "nebyl" in e["message"]
    assert Payment.objects.count() == 0


def test_every_call_is_audited(mcp, issued, profile):
    mcp.call("get_invoice", profile="f13", invoice_id=issued.pk)
    mcp.call("get_invoice", profile="neexistuje", invoice_id=1)
    events = list(AuditEvent.objects.filter(action="mcp.call").order_by("id"))
    assert [e.after["ok"] for e in events] == [True, False]
    assert events[0].profile == profile and events[0].interface == "mcp" and events[0].actor.is_admin
    assert events[1].after["error"] == "not_found"


# --- ARES a partneři ------------------------------------------------------------------------

def test_ares_mapping_and_checksum():
    from partners import ares

    data = ares.to_partner_data(ARES_F13)
    assert data == {**data, "name": "F13 CyberTech s.r.o.", "ico": "06646654", "dic": "CZ06646654",
                    "vat_payer": True, "street": "Sokola Tůmy 1099/1", "city": "Ostrava - Hulváky",
                    "zip_code": "709 00", "country": "Česká republika"}
    assert ares.ico_checksum_ok("06646654") and ares.ico_checksum_ok("02963281")
    assert not ares.ico_checksum_ok("06646655")
    with pytest.raises(ares.AresError):
        ares.lookup("12345678")


def test_create_partner_from_ares(mcp, profile, monkeypatch):
    from partners import ares

    calls = []
    monkeypatch.setattr(ares, "_request", lambda url, payload=None: calls.append(url) or ARES_F13)
    args = {"profile": "f13", "ico": "06646654", "billing_email": "fakturace@f13.cz", "client_request_id": rid()}
    data = ok(mcp.call("create_partner", **args))
    assert data["source"] == "ARES" and data["partner"]["name"] == "F13 CyberTech s.r.o."
    assert data["partner"]["billing_email"] == "fakturace@f13.cz" and data["partner"]["profile"]["slug"] == "f13"
    p = Partner.objects.get()
    assert (p.street, p.city, p.zip_code, p.vat_payer) == ("Sokola Tůmy 1099/1", "Ostrava - Hulváky", "709 00", True)
    dup = err(mcp.call("create_partner", **{**args, "client_request_id": rid()}), "duplicate")
    assert dup["existing"][0]["id"] == p.pk
    assert AuditEvent.objects.filter(action="partner.create", interface="mcp").exists()


def test_lookup_company_ares_errors(mcp, monkeypatch):
    from partners import ares

    monkeypatch.setattr(ares, "_request", lambda url, payload=None: None)
    err(mcp.call("lookup_company_ares", ico="06646654"), "not_found")

    def boom(url, payload=None):
        raise ares.AresError("ARES je momentálně nedostupný, zkuste to později.")

    monkeypatch.setattr(ares, "_request", boom)
    err(mcp.call("lookup_company_ares", name="F13 Cyber"), "external_service_error")
    err(mcp.call("lookup_company_ares"), "invalid_arguments")


def test_web_partner_form_prefills_from_ares(admin, profile, monkeypatch):
    from partners import ares

    monkeypatch.setattr(ares, "_request", lambda url, payload=None: ARES_F13)
    response = client_for(admin).get(f"/p/{profile.slug}/partneri/novy/?ares=06646654")
    text = response.content.decode()
    assert 'value="F13 CyberTech s.r.o."' in text and 'value="Sokola Tůmy 1099/1"' in text
    assert "předvyplněny z ARES" in text
    assert not Partner.objects.exists()  # nic se neuložilo bez potvrzení


# --- oficiální MCP klient --------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_official_mcp_client(live_server, make_user, make_profile, make_partner):
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    profile = make_profile("f13", "06646654", name="F13 CyberTech s.r.o.")
    partner = make_partner(profile)
    admin = make_user("admin@test.cz", admin=True)
    inv = services.create_draft(Actor(user=admin), profile, header_for(profile, partner), [line()])
    services.issue(Actor(user=admin), inv, inv.version)

    async def run():
        headers = {"Cf-Access-Jwt-Assertion": make_token(admin.email)}
        async with streamablehttp_client(f"{live_server.url}/mcp/", headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("find_last_invoice", {"partner": "DvB-AF"})
                return init, tools, result

    # Vlastní vlákno: v jedné relaci s Playwrightem už v hlavním vlákně běží asyncio smyčka.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=1) as pool:
        init, tools, result = pool.submit(anyio.run, run).result(timeout=60)
    assert init.serverInfo.name == "faktury"
    assert len(tools.tools) >= 15
    assert result.isError is False
    assert result.structuredContent["invoice"]["number"] == "261"
    assert result.structuredContent["profile"]["name"] == "F13 CyberTech s.r.o."


def test_schema_document_is_current():
    from pathlib import Path

    from django.conf import settings

    from mcpapi.schema_doc import render

    doc = Path(settings.BASE_DIR).parent / "fakturační aplikace" / "mcp-schema.md"
    assert doc.read_text(encoding="utf-8") == render(), "Spusťte: python src/manage.py mcp_schema"
