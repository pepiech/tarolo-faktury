"""MCP (Model Context Protocol) přes Streamable HTTP – bezstavový server s JSON odpověďmi.

Podporované metody: initialize, ping, tools/list, tools/call; notifikace se potvrdí 202."""

import hashlib
import json
import logging

import jsonschema
from django.db import connection, transaction

from accounts.permissions import Actor
from audit import services as audit

from . import errors as E
from .errors import ToolError
from .models import IdempotencyRecord
from .tools import SCHEMA_VERSION, TOOLS, Ctx

logger = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
SERVER_INFO = {"name": "faktury", "title": "Faktury – správa faktur", "version": SCHEMA_VERSION}
INSTRUCTIONS = """Fakturační aplikace s více vystavujícími profily (např. F13 CyberTech s.r.o., Martin Piech).
Pravidla:
- Každý výsledek uvádí vystavující profil; v odpovědi uživateli ho vždy jmenuj.
- Zápisy (návrh, vystavení, storno, platba, partner) vyžadují výslovný profil. Pokud ho uživatel neuvedl
  a má přístup k více profilům, zeptej se, za koho operaci provést. Profil nikdy neodhaduj z partnera.
- Při chybě 'ambiguous' nevybírej sám – ukaž kandidáty a zeptej se.
- „Vytvoř fakturu“ znamená jen NÁVRH (create_invoice_draft). Vystavit (issue_invoice) nebo stornovat
  (cancel_invoice) smíš až po výslovném pokynu uživatele a ukázání rekapitulace, s confirm=true.
- Ceny položek jsou bez DPH. U dotazu na zaplacení uveď profil, partnera, číslo a datum faktury, částku,
  zbývající částku, splatnost, stav úhrady a čas/zdroj poslední platby.
- Ke každému zápisu vygeneruj nový client_request_id; při opakování téhož požadavku použij stejný."""

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _error(msg_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def tools_list():
    return [
        {"name": t.name, "title": t.title, "description": t.description, "inputSchema": t.input_schema,
         "annotations": {"title": t.title, **t.annotations}}
        for t in TOOLS.values()
    ]


def _content(structured, is_error=False):
    extra = structured.pop("_content", [])
    text = json.dumps(structured, ensure_ascii=False, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}, *extra], "structuredContent": structured,
            "isError": is_error}


def _request_hash(args):
    payload = {k: v for k, v in args.items() if k != "client_request_id"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _run_write(ctx, tool, args):
    key = args["client_request_id"]
    digest = _request_hash(args)
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           [f"mcp:{ctx.user.pk}:{tool.name}:{key}"])
        record = IdempotencyRecord.objects.filter(user=ctx.user, tool=tool.name, client_request_id=key).first()
        if record is not None:
            if record.request_hash != digest:
                raise ToolError(E.IDEMPOTENCY_CONFLICT,
                                "Stejný client_request_id už byl použit s jinými argumenty. Pro nový požadavek "
                                "vygeneruj nový klíč.")
            return {**record.response, "idempotent_replay": True}
        result = tool.handler(ctx, args)
        stored = json.loads(json.dumps(result, default=str))
        IdempotencyRecord.objects.create(user=ctx.user, tool=tool.name, client_request_id=key,
                                         request_hash=digest, response=stored)
        return stored


def _audit_args(args):
    text = json.dumps(args, ensure_ascii=False, default=str)
    return text if len(text) <= 2000 else text[:2000] + "…"


def call_tool(request, name, args):
    tool = TOOLS[name]
    actor = Actor(user=request.user, interface="mcp", request_id=getattr(request, "request_id", ""))
    ctx = Ctx(request=request, user=request.user, actor=actor)
    error_code = None
    try:
        try:
            jsonschema.validate(args, tool.input_schema)
        except jsonschema.ValidationError as exc:
            path = ".".join(str(p) for p in exc.absolute_path) or "(argumenty)"
            raise ToolError(E.INVALID_ARGUMENTS, f"Neplatný argument {path}: {exc.message}") from exc
        result = _run_write(ctx, tool, args) if tool.write else tool.handler(ctx, args)
        return _content(result)
    except ToolError as exc:
        error_code = exc.code
        return _content(exc.as_dict(), is_error=True)
    except Exception:
        error_code = "internal_error"
        raise
    finally:
        audit.record(actor, "mcp.call", profile=ctx.profile,
                     after={"tool": name, "ok": error_code is None, "error": error_code},
                     note=_audit_args(args))


def handle(request, message):
    """Zpracuje jednu JSON-RPC zprávu; vrátí odpověď, nebo None u notifikace."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None, INVALID_REQUEST, "Invalid Request")
    method = message["method"]
    params = message.get("params") or {}
    if "id" not in message:
        return None  # notifikace (notifications/initialized, notifications/cancelled, …)
    msg_id = message["id"]
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return _result(msg_id, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": tools_list()})
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
        try:
            return _result(msg_id, call_tool(request, name, args))
        except Exception:
            logger.exception("MCP nástroj %s selhal", name)
            return _error(msg_id, INTERNAL_ERROR, "Internal error")
    return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")
