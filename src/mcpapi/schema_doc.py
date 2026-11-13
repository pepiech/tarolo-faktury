"""Generuje verzovaný popis MCP rozhraní (fakturační aplikace/mcp-schema.md) přímo z kódu."""

import json

from . import errors
from .protocol import INSTRUCTIONS, SERVER_INFO, SUPPORTED_VERSIONS
from .tools import SCHEMA_VERSION, TOOLS

ERROR_CODES = {
    errors.INVALID_ARGUMENTS: "argumenty neodpovídají schématu nástroje",
    errors.PROFILE_REQUIRED: "chybí výslovný profil; v `available_profiles` jsou možnosti – zeptat se uživatele",
    errors.NOT_FOUND: "záznam neexistuje nebo k němu uživatel nemá přístup",
    errors.FORBIDDEN: "profil je přístupný, ale chybí potřebné oprávnění",
    errors.AMBIGUOUS: "dotazu odpovídá více záznamů; `candidates` – nevybírat svévolně, zeptat se",
    "duplicate": "podobný partner už v profilu existuje (`existing`)",
    errors.VALIDATION_ERROR: "obchodní pravidlo (např. neplatná sazba, vystavený doklad nelze upravit)",
    errors.VERSION_CONFLICT: "záznam mezitím změnil někdo jiný (optimistický zámek) – načíst znovu",
    errors.CONFIRMATION_REQUIRED: "nevratná operace bez `confirm=true`; `recap` ukázat uživateli",
    errors.IDEMPOTENCY_CONFLICT: "stejný `client_request_id` s jinými argumenty",
    errors.EXTERNAL_SERVICE: "nedostupná externí služba (ARES)",
}


def render():
    out = [
        f"# MCP rozhraní fakturační aplikace – schéma {SCHEMA_VERSION}",
        "",
        "> Generováno z kódu příkazem `python src/manage.py mcp_schema`. Neupravovat ručně.",
        "",
        "## Připojení",
        "",
        "- Endpoint: `https://<hostname>/mcp/` (MCP Streamable HTTP, bezstavový, odpovědi `application/json`, bez SSE).",
        f"- Podporované verze protokolu: {', '.join(f'`{v}`' for v in SUPPORTED_VERSIONS)}.",
        f"- Server: `{SERVER_INFO['name']}` {SERVER_INFO['version']}.",
        "- Ověření: Cloudflare Access s Managed OAuth (MCP klient projde OAuth přihlášením Cloudflare); "
        "aplikace ověří `Cf-Access-Jwt-Assertion` a uživatele stejně jako u webu. Platí stejná oprávnění "
        "po profilech (`invoices:view`, `invoices:modify`, `partners:modify`, admin).",
        "- Každé volání nástroje se zapíše do auditu (`mcp.call`, rozhraní `mcp`); zápisy navíc vlastní "
        "auditní události (např. `invoice.issue`).",
        "",
        "## Pravidla pro AI klienta (instrukce serveru)",
        "",
        "```",
        INSTRUCTIONS,
        "```",
        "",
        "## Výsledky a chyby",
        "",
        "- Výsledek nástroje: `structuredContent` (JSON objekt) + stejný obsah jako text. Každý doklad i "
        "partner nese `profile` = `{id, slug, name, ico}`.",
        "- Částky jsou řetězce s tečkou (`\"1936.00\"`), měna CZK; data ve tvaru `RRRR-MM-DD`.",
        "- Doklad: `status` (`draft`/`issued`/`cancelled`), `is_draft`, `number` (u návrhu `null`), "
        "`version` (pro `expected_version`), u vystavených `payment_state`, `remaining`, `overdue`, "
        "`last_payment` (datum, částka, `source`, `recorded_at`).",
        "- Zápisy vyžadují `client_request_id`; opakování se stejným klíčem a argumenty vrátí původní "
        "výsledek s `idempotent_replay: true`.",
        "- Chyba nástroje: `isError: true`, `structuredContent.error = {code, message, …}`. Kódy:",
        "",
        "| Kód | Význam |",
        "|---|---|",
        *[f"| `{code}` | {text} |" for code, text in ERROR_CODES.items()],
        "",
        "## Nástroje",
        "",
    ]
    for t in TOOLS.values():
        kind = "zápis" if t.write else "čtení"
        flags = ", ".join(k for k, v in t.annotations.items() if v is True) or "–"
        out += [
            f"### `{t.name}` – {t.title}",
            "",
            f"*{kind}; anotace: {flags}*",
            "",
            t.description,
            "",
            "```json",
            json.dumps(t.input_schema, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    return "\n".join(out).rstrip() + "\n"
