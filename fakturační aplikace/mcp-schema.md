# MCP rozhraní fakturační aplikace – schéma 1.1.0

> Generováno z kódu příkazem `python src/manage.py mcp_schema`. Neupravovat ručně.

## Připojení

- Endpoint: `https://<hostname>/mcp/` (MCP Streamable HTTP, bezstavový, odpovědi `application/json`, bez SSE).
- Podporované verze protokolu: `2025-11-25`, `2025-06-18`, `2025-03-26`.
- Server: `faktury` 1.1.0.
- Ověření: Cloudflare Access s Managed OAuth (MCP klient projde OAuth přihlášením Cloudflare); aplikace ověří `Cf-Access-Jwt-Assertion` a uživatele stejně jako u webu. Platí stejná oprávnění po profilech (`invoices:view`, `invoices:modify`, `partners:modify`, admin).
- Každé volání nástroje se zapíše do auditu (`mcp.call`, rozhraní `mcp`); zápisy navíc vlastní auditní události (např. `invoice.issue`).

## Pravidla pro AI klienta (instrukce serveru)

```
Fakturační aplikace s více vystavujícími profily (např. F13 CyberTech s.r.o., Martin Piech).
Pravidla:
- Každý výsledek uvádí vystavující profil; v odpovědi uživateli ho vždy jmenuj.
- Zápisy (návrh, vystavení, storno, platba, partner) vyžadují výslovný profil. Pokud ho uživatel neuvedl
  a má přístup k více profilům, zeptej se, za koho operaci provést. Profil nikdy neodhaduj z partnera.
- Při chybě 'ambiguous' nevybírej sám – ukaž kandidáty a zeptej se.
- „Vytvoř fakturu“ znamená jen NÁVRH (create_invoice_draft). Vystavit (issue_invoice) nebo stornovat
  (cancel_invoice) smíš až po výslovném pokynu uživatele a ukázání rekapitulace, s confirm=true.
- Ceny položek jsou bez DPH. U dotazu na zaplacení uveď profil, partnera, číslo a datum faktury, částku,
  zbývající částku, splatnost, stav úhrady a čas/zdroj poslední platby.
- Ke každému zápisu vygeneruj nový client_request_id; při opakování téhož požadavku použij stejný.
- Na počty a součty použij total_count/totals_by_profile ze search_invoices, nástroj invoice_stats
  (seskupení podle partnera, měsíce, stavu) nebo list_payments – neprocházej ručně stránky.
- Relativní období („tento týden“, „minulý měsíc“) zadávej parametrem period, server ho přepočítá
  v pražském čase. „Uhrazené za období“ = payment_state=paid + period_field=paid_on (datum platby).
```

## Výsledky a chyby

- Výsledek nástroje: `structuredContent` (JSON objekt) + stejný obsah jako text. Každý doklad i partner nese `profile` = `{id, slug, name, ico}`.
- Částky jsou řetězce s tečkou (`"1936.00"`), měna CZK; data ve tvaru `RRRR-MM-DD`.
- Doklad: `status` (`draft`/`issued`/`cancelled`), `is_draft`, `number` (u návrhu `null`), `version` (pro `expected_version`), u vystavených `payment_state`, `remaining`, `overdue`, `last_payment` (datum, částka, `source`, `recorded_at`).
- Zápisy vyžadují `client_request_id`; opakování se stejným klíčem a argumenty vrátí původní výsledek s `idempotent_replay: true`.
- Chyba nástroje: `isError: true`, `structuredContent.error = {code, message, …}`. Kódy:

| Kód | Význam |
|---|---|
| `invalid_arguments` | argumenty neodpovídají schématu nástroje |
| `profile_required` | chybí výslovný profil; v `available_profiles` jsou možnosti – zeptat se uživatele |
| `not_found` | záznam neexistuje nebo k němu uživatel nemá přístup |
| `forbidden` | profil je přístupný, ale chybí potřebné oprávnění |
| `ambiguous` | dotazu odpovídá více záznamů; `candidates` – nevybírat svévolně, zeptat se |
| `duplicate` | podobný partner už v profilu existuje (`existing`) |
| `validation_error` | obchodní pravidlo (např. neplatná sazba, vystavený doklad nelze upravit) |
| `version_conflict` | záznam mezitím změnil někdo jiný (optimistický zámek) – načíst znovu |
| `confirmation_required` | nevratná operace bez `confirm=true`; `recap` ukázat uživateli |
| `idempotency_conflict` | stejný `client_request_id` s jinými argumenty |
| `external_service_error` | nedostupná externí služba (ARES) |

## Nástroje

### `list_profiles` – Seznam fakturačních profilů

*čtení; anotace: readOnlyHint*

Vrátí fakturační profily (vystavitele), ke kterým má uživatel přístup, s jeho oprávněními, sazbami a režimy DPH, účty a číselnými řadami. Použij na začátku, když není jasné, za koho se má dotaz nebo změna provést.

```json
{
  "type": "object",
  "properties": {},
  "required": [],
  "additionalProperties": false
}
```

### `search_partners` – Hledání partnerů

*čtení; anotace: readOnlyHint*

Vyhledá odběratele/dodavatele podle názvu, IČO nebo DIČ. Bez profilu hledá ve všech přístupných profilech a výsledky rozdělí podle profilu. Stránkované.

```json
{
  "type": "object",
  "properties": {
    "query": {
      "type": "string",
      "minLength": 2,
      "maxLength": 100
    },
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "include_inactive": {
      "type": "boolean",
      "default": false
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 50,
      "default": 20
    },
    "cursor": {
      "type": "string",
      "description": "Hodnota next_cursor z předchozí stránky"
    }
  },
  "required": [
    "query"
  ],
  "additionalProperties": false
}
```

### `get_partner` – Detail partnera

*čtení; anotace: readOnlyHint*

Detail partnera v daném profilu včetně posledních dokladů.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "partner_id": {
      "type": "integer"
    }
  },
  "required": [
    "partner_id"
  ],
  "additionalProperties": false
}
```

### `search_invoices` – Hledání faktur

*čtení; anotace: readOnlyHint*

Vyhledá doklady (návrhy i vystavené) podle čísla, odběratele, stavu, úhrady, splatnosti, data platby a období (period). Vrací total_count a souhrny po profilech přes VŠECHNY nalezené doklady (ne jen stránku) – pro otázky typu „kolik faktur…“ použij tyto hodnoty. compact=true vrací úsporné řádky. Bez profilu hledá ve všech profilech s oprávněním invoices:view a výsledky i součty rozdělí podle profilu (částky profilů nikdy nesčítej).

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "query": {
      "type": "string",
      "maxLength": 100,
      "description": "Číslo dokladu, VS nebo část názvu odběratele"
    },
    "partner_id": {
      "type": "integer"
    },
    "partner_ico": {
      "type": "string",
      "pattern": "^\\d{1,8}$"
    },
    "status": {
      "type": "string",
      "enum": [
        "draft",
        "issued",
        "cancelled"
      ]
    },
    "payment_state": {
      "type": "string",
      "enum": [
        "unpaid",
        "partial",
        "paid",
        "overpaid"
      ]
    },
    "overdue_only": {
      "type": "boolean",
      "default": false
    },
    "issued_from": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "issued_to": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "due_from": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "due_to": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "paid_from": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Faktury s (nestornovanou) platbou s datem úhrady od – RRRR-MM-DD"
    },
    "paid_to": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Faktury s (nestornovanou) platbou s datem úhrady do – RRRR-MM-DD"
    },
    "period": {
      "type": "string",
      "enum": [
        "today",
        "yesterday",
        "this_week",
        "last_week",
        "last_7_days",
        "this_month",
        "last_month",
        "last_30_days",
        "this_quarter",
        "last_quarter",
        "this_year",
        "last_year"
      ],
      "description": "Pojmenované období počítané na serveru (Europe/Prague, týden od pondělí): today, yesterday, this_week, last_week, last_7_days, this_month, last_month, last_30_days, this_quarter, last_quarter, this_year, last_year. Přednost mají výslovná data."
    },
    "period_field": {
      "type": "string",
      "enum": [
        "issue_date",
        "due_date",
        "paid_on"
      ],
      "default": "issue_date",
      "description": "Na které datum se period vztahuje: issue_date, due_date, paid_on"
    },
    "sort": {
      "type": "string",
      "enum": [
        "issue_date_desc",
        "issue_date_asc",
        "due_date_asc",
        "total_desc",
        "remaining_desc"
      ],
      "default": "issue_date_desc"
    },
    "compact": {
      "type": "boolean",
      "default": false
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 50,
      "default": 20
    },
    "cursor": {
      "type": "string",
      "description": "Hodnota next_cursor z předchozí stránky"
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `invoice_stats` – Statistiky faktur

*čtení; anotace: readOnlyHint*

Počty a součty dokladů seskupené podle partnera, měsíce vystavení, stavu úhrady nebo stavu dokladu – vždy zvlášť pro každý profil. Hodí se na „kolikrát jsme fakturovali X“, „obrat po měsících“, „kdo dluží nejvíc“. Bez status se počítají jen vystavené doklady (bez návrhů a stornovaných). Filtry jako search_invoices.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "query": {
      "type": "string",
      "maxLength": 100,
      "description": "Číslo dokladu, VS nebo část názvu odběratele"
    },
    "partner_id": {
      "type": "integer"
    },
    "partner_ico": {
      "type": "string",
      "pattern": "^\\d{1,8}$"
    },
    "status": {
      "type": "string",
      "enum": [
        "draft",
        "issued",
        "cancelled"
      ]
    },
    "payment_state": {
      "type": "string",
      "enum": [
        "unpaid",
        "partial",
        "paid",
        "overpaid"
      ]
    },
    "overdue_only": {
      "type": "boolean",
      "default": false
    },
    "issued_from": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "issued_to": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "due_from": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "due_to": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "paid_from": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Faktury s (nestornovanou) platbou s datem úhrady od – RRRR-MM-DD"
    },
    "paid_to": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Faktury s (nestornovanou) platbou s datem úhrady do – RRRR-MM-DD"
    },
    "period": {
      "type": "string",
      "enum": [
        "today",
        "yesterday",
        "this_week",
        "last_week",
        "last_7_days",
        "this_month",
        "last_month",
        "last_30_days",
        "this_quarter",
        "last_quarter",
        "this_year",
        "last_year"
      ],
      "description": "Pojmenované období počítané na serveru (Europe/Prague, týden od pondělí): today, yesterday, this_week, last_week, last_7_days, this_month, last_month, last_30_days, this_quarter, last_quarter, this_year, last_year. Přednost mají výslovná data."
    },
    "period_field": {
      "type": "string",
      "enum": [
        "issue_date",
        "due_date",
        "paid_on"
      ],
      "default": "issue_date",
      "description": "Na které datum se period vztahuje: issue_date, due_date, paid_on"
    },
    "group_by": {
      "type": "string",
      "enum": [
        "partner",
        "month",
        "payment_state",
        "status",
        "none"
      ],
      "default": "none"
    },
    "top": {
      "type": "integer",
      "minimum": 1,
      "maximum": 100,
      "default": 20,
      "description": "Max. počet skupin na profil (u partnerů řazeno podle částky)"
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `list_payments` – Přijaté platby

*čtení; anotace: readOnlyHint*

Seznam zapsaných plateb za období (podle data úhrady) se součtem po profilech – např. „kolik přišlo peněz tento týden“. Protioperace (opravy) jsou záporné a v součtu se odečtou.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "period": {
      "type": "string",
      "enum": [
        "today",
        "yesterday",
        "this_week",
        "last_week",
        "last_7_days",
        "this_month",
        "last_month",
        "last_30_days",
        "this_quarter",
        "last_quarter",
        "this_year",
        "last_year"
      ],
      "description": "Pojmenované období počítané na serveru (Europe/Prague, týden od pondělí): today, yesterday, this_week, last_week, last_7_days, this_month, last_month, last_30_days, this_quarter, last_quarter, this_year, last_year. Přednost mají výslovná data."
    },
    "paid_from": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "paid_to": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "partner_id": {
      "type": "integer"
    },
    "method": {
      "type": "string",
      "enum": [
        "bank_transfer",
        "cash",
        "card",
        "other"
      ]
    },
    "limit": {
      "type": "integer",
      "minimum": 1,
      "maximum": 50,
      "default": 20
    },
    "cursor": {
      "type": "string",
      "description": "Hodnota next_cursor z předchozí stránky"
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `get_invoice` – Detail dokladu

*čtení; anotace: readOnlyHint*

Detail dokladu: vystavitel, odběratel, položky, částky, stav úhrady včetně platby a jejího zdroje a času, splatnost a odkazy na web/PDF.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "invoice_id": {
      "type": "integer"
    }
  },
  "required": [
    "invoice_id"
  ],
  "additionalProperties": false
}
```

### `find_last_invoice` – Poslední faktura partnera

*čtení; anotace: readOnlyHint*

Najde poslední vystavenou fakturu partnera a vrátí její stav úhrady (zbývající částka, splatnost, čas a zdroj poslední platby). Pokud název odpovídá více partnerům nebo stejnému partnerovi ve více profilech, vrátí chybu 'ambiguous' s kandidáty – uživatele se pak zeptej a zavolej znovu s partner_id a profilem.

```json
{
  "type": "object",
  "properties": {
    "partner": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Název/IČO partnera nebo partner_id"
    },
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    }
  },
  "required": [
    "partner"
  ],
  "additionalProperties": false
}
```

### `get_profile_summary` – Souhrn profilu

*čtení; anotace: readOnlyHint*

Souhrny jednoho profilu v CZK: vystaveno, uhrazeno, zbývá, po splatnosti, rozpracované návrhy a faktury k upomenutí. Částky různých profilů se nikdy nesčítají.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "year": {
      "type": "integer",
      "minimum": 2000,
      "maximum": 2100
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `lookup_company_ares` – Vyhledání firmy v ARES

*čtení; anotace: readOnlyHint*

Dohledá ekonomický subjekt v registru ARES podle IČO nebo názvu (jen čtení, nic neukládá). Výsledek lze předat do create_partner.

```json
{
  "type": "object",
  "properties": {
    "ico": {
      "type": "string",
      "pattern": "^\\d{1,8}$"
    },
    "name": {
      "type": "string",
      "minLength": 3,
      "maxLength": 100
    }
  },
  "required": [],
  "additionalProperties": false
}
```

### `preview_invoice_draft` – Náhled návrhu

*čtení; anotace: readOnlyHint*

Vygeneruje náhled návrhu faktury (bez čísla, s vodoznakem NÁHLED) a vrátí rekapitulaci a odkaz na PDF. include_pdf=true přiloží PDF přímo do odpovědi.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "invoice_id": {
      "type": "integer"
    },
    "include_pdf": {
      "type": "boolean",
      "default": false
    }
  },
  "required": [
    "invoice_id"
  ],
  "additionalProperties": false
}
```

### `create_partner` – Založení partnera

*zápis; anotace: idempotentHint*

Založí odběratele ve výslovně zvoleném profilu. S IČO se údaje načtou z ARES (use_ares=true); výslovně zadaná pole mají přednost. Pokud partner se stejným IČO v profilu existuje, vrátí chybu 'duplicate' s existujícím záznamem.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "ico": {
      "type": "string",
      "pattern": "^\\d{1,8}$"
    },
    "name": {
      "type": "string",
      "maxLength": 200
    },
    "dic": {
      "type": "string",
      "maxLength": 14
    },
    "street": {
      "type": "string",
      "maxLength": 200
    },
    "city": {
      "type": "string",
      "maxLength": 100
    },
    "zip_code": {
      "type": "string",
      "maxLength": 10
    },
    "country": {
      "type": "string",
      "maxLength": 60
    },
    "vat_payer": {
      "type": "boolean"
    },
    "billing_email": {
      "type": "string",
      "maxLength": 254
    },
    "contact_person": {
      "type": "string",
      "maxLength": 200
    },
    "phone": {
      "type": "string",
      "maxLength": 40
    },
    "due_days_override": {
      "type": "integer",
      "minimum": 0,
      "maximum": 365
    },
    "use_ares": {
      "type": "boolean",
      "default": true
    },
    "allow_duplicate": {
      "type": "boolean",
      "default": false
    },
    "client_request_id": {
      "type": "string",
      "minLength": 8,
      "maxLength": 120,
      "description": "Jedinečný klíč požadavku (např. UUID). Opakování se stejným klíčem a stejnými argumenty vrátí původní výsledek a nic nezaloží znovu."
    }
  },
  "required": [
    "client_request_id"
  ],
  "additionalProperties": false
}
```

### `create_invoice_draft` – Vytvoření návrhu faktury

*zápis; anotace: idempotentHint*

Vytvoří NÁVRH faktury (nevystaví ho, nepřidělí číslo) ve výslovně zvoleném profilu a vrátí rekapitulaci včetně vystavitele, účtu a odkazu na náhled. Ceny jsou bez DPH.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "partner_id": {
      "type": "integer",
      "description": "Id odběratele z search_partners (stejný profil)."
    },
    "lines": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "description": {
            "type": "string",
            "minLength": 1,
            "maxLength": 300
          },
          "quantity": {
            "type": [
              "string",
              "number"
            ],
            "default": 1
          },
          "unit": {
            "type": "string",
            "maxLength": 12
          },
          "unit_price": {
            "type": [
              "string",
              "number"
            ],
            "description": "Cena za jednotku BEZ DPH v Kč. Pokud uživatel zadal cenu s DPH, zeptej se nebo ji přepočítej až po jeho potvrzení."
          },
          "discount_percent": {
            "type": [
              "string",
              "number"
            ],
            "default": 0
          },
          "vat_rate_percent": {
            "type": [
              "string",
              "number"
            ],
            "description": "Sazba DPH z nabídky profilu (list_profiles → vat_rates). Výchozí je první aktivní sazba profilu."
          }
        },
        "required": [
          "description",
          "unit_price"
        ],
        "additionalProperties": false
      },
      "minItems": 1,
      "maxItems": 100
    },
    "issue_date": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "taxable_supply_date": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "due_date": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "due_days": {
      "type": "integer",
      "minimum": 0,
      "maximum": 365,
      "description": "Splatnost ve dnech od data vystavení (místo due_date)."
    },
    "payment_method": {
      "type": "string",
      "enum": [
        "bank_transfer",
        "cash",
        "card",
        "other"
      ]
    },
    "bank_account_id": {
      "type": "integer"
    },
    "vat_regime": {
      "type": "string",
      "description": "Kód režimu DPH profilu (list_profiles → vat_regimes)."
    },
    "series_id": {
      "type": "integer"
    },
    "variable_symbol": {
      "type": "string",
      "pattern": "^\\d{1,10}$",
      "description": "Jen u profilů s ručním VS; jinak se přidělí z čísla faktury."
    },
    "constant_symbol": {
      "type": "string",
      "pattern": "^\\d{1,4}$"
    },
    "specific_symbol": {
      "type": "string",
      "pattern": "^\\d{1,10}$"
    },
    "order_reference": {
      "type": "string",
      "maxLength": 100
    },
    "public_note": {
      "type": "string",
      "maxLength": 2000
    },
    "internal_note": {
      "type": "string",
      "maxLength": 2000
    },
    "client_request_id": {
      "type": "string",
      "minLength": 8,
      "maxLength": 120,
      "description": "Jedinečný klíč požadavku (např. UUID). Opakování se stejným klíčem a stejnými argumenty vrátí původní výsledek a nic nezaloží znovu."
    }
  },
  "required": [
    "partner_id",
    "lines",
    "client_request_id"
  ],
  "additionalProperties": false
}
```

### `update_invoice_draft` – Úprava návrhu faktury

*zápis; anotace: idempotentHint*

Upraví návrh faktury. Uvedená pole se přepíšou, lines (pokud jsou uvedeny) nahradí všechny položky. Vyžaduje expected_version z posledního načtení (optimistický zámek). Vystavený doklad upravit nelze.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "invoice_id": {
      "type": "integer"
    },
    "expected_version": {
      "type": "integer"
    },
    "partner_id": {
      "type": "integer",
      "description": "Id odběratele z search_partners (stejný profil)."
    },
    "lines": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "description": {
            "type": "string",
            "minLength": 1,
            "maxLength": 300
          },
          "quantity": {
            "type": [
              "string",
              "number"
            ],
            "default": 1
          },
          "unit": {
            "type": "string",
            "maxLength": 12
          },
          "unit_price": {
            "type": [
              "string",
              "number"
            ],
            "description": "Cena za jednotku BEZ DPH v Kč. Pokud uživatel zadal cenu s DPH, zeptej se nebo ji přepočítej až po jeho potvrzení."
          },
          "discount_percent": {
            "type": [
              "string",
              "number"
            ],
            "default": 0
          },
          "vat_rate_percent": {
            "type": [
              "string",
              "number"
            ],
            "description": "Sazba DPH z nabídky profilu (list_profiles → vat_rates). Výchozí je první aktivní sazba profilu."
          }
        },
        "required": [
          "description",
          "unit_price"
        ],
        "additionalProperties": false
      },
      "minItems": 1,
      "maxItems": 100
    },
    "issue_date": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "taxable_supply_date": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "due_date": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "due_days": {
      "type": "integer",
      "minimum": 0,
      "maximum": 365,
      "description": "Splatnost ve dnech od data vystavení (místo due_date)."
    },
    "payment_method": {
      "type": "string",
      "enum": [
        "bank_transfer",
        "cash",
        "card",
        "other"
      ]
    },
    "bank_account_id": {
      "type": "integer"
    },
    "vat_regime": {
      "type": "string",
      "description": "Kód režimu DPH profilu (list_profiles → vat_regimes)."
    },
    "series_id": {
      "type": "integer"
    },
    "variable_symbol": {
      "type": "string",
      "pattern": "^\\d{1,10}$",
      "description": "Jen u profilů s ručním VS; jinak se přidělí z čísla faktury."
    },
    "constant_symbol": {
      "type": "string",
      "pattern": "^\\d{1,4}$"
    },
    "specific_symbol": {
      "type": "string",
      "pattern": "^\\d{1,10}$"
    },
    "order_reference": {
      "type": "string",
      "maxLength": 100
    },
    "public_note": {
      "type": "string",
      "maxLength": 2000
    },
    "internal_note": {
      "type": "string",
      "maxLength": 2000
    },
    "client_request_id": {
      "type": "string",
      "minLength": 8,
      "maxLength": 120,
      "description": "Jedinečný klíč požadavku (např. UUID). Opakování se stejným klíčem a stejnými argumenty vrátí původní výsledek a nic nezaloží znovu."
    }
  },
  "required": [
    "invoice_id",
    "expected_version",
    "client_request_id"
  ],
  "additionalProperties": false
}
```

### `issue_invoice` – Vystavení faktury

*zápis; anotace: destructiveHint, idempotentHint*

Vystaví návrh: přidělí číslo a VS, zmrazí údaje a uloží PDF. Nevratné – volej jen na výslovný pokyn uživatele po ukázání rekapitulace, s confirm=true. Bez confirm vrátí rekapitulaci (chyba confirmation_required).

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "invoice_id": {
      "type": "integer"
    },
    "expected_version": {
      "type": "integer"
    },
    "confirm": {
      "type": "boolean",
      "description": "true = uživatel výslovně potvrdil vystavení"
    },
    "client_request_id": {
      "type": "string",
      "minLength": 8,
      "maxLength": 120,
      "description": "Jedinečný klíč požadavku (např. UUID). Opakování se stejným klíčem a stejnými argumenty vrátí původní výsledek a nic nezaloží znovu."
    }
  },
  "required": [
    "invoice_id",
    "expected_version",
    "client_request_id"
  ],
  "additionalProperties": false
}
```

### `cancel_invoice` – Storno faktury

*zápis; anotace: destructiveHint, idempotentHint*

Stornuje vystavenou fakturu bez evidované úhrady (číslo se znovu nepoužije). Nevratné – jen na výslovný pokyn uživatele s důvodem a confirm=true.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "invoice_id": {
      "type": "integer"
    },
    "expected_version": {
      "type": "integer"
    },
    "reason": {
      "type": "string",
      "minLength": 3,
      "maxLength": 500
    },
    "confirm": {
      "type": "boolean"
    },
    "client_request_id": {
      "type": "string",
      "minLength": 8,
      "maxLength": 120,
      "description": "Jedinečný klíč požadavku (např. UUID). Opakování se stejným klíčem a stejnými argumenty vrátí původní výsledek a nic nezaloží znovu."
    }
  },
  "required": [
    "invoice_id",
    "expected_version",
    "reason",
    "client_request_id"
  ],
  "additionalProperties": false
}
```

### `record_payment` – Zápis platby

*zápis; anotace: idempotentHint*

Zapíše přijatou platbu k vystavené faktuře (částečnou, plnou i přeplatek) a vrátí nový stav úhrady. Opravu chybné platby řeší protioperace ve webu.

```json
{
  "type": "object",
  "properties": {
    "profile": {
      "type": [
        "string",
        "integer"
      ],
      "description": "Vystavující profil: id, zkratka (slug) nebo IČO přesně podle list_profiles. Nikdy jej neodhaduj – při nejasnosti se uživatele zeptej."
    },
    "invoice_id": {
      "type": "integer"
    },
    "amount": {
      "type": [
        "string",
        "number"
      ],
      "description": "Částka v Kč, desetinná tečka (např. \"1936.00\")"
    },
    "paid_on": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Datum ve tvaru RRRR-MM-DD"
    },
    "method": {
      "type": "string",
      "enum": [
        "bank_transfer",
        "cash",
        "card",
        "other"
      ]
    },
    "reference": {
      "type": "string",
      "maxLength": 100
    },
    "note": {
      "type": "string",
      "maxLength": 255
    },
    "client_request_id": {
      "type": "string",
      "minLength": 8,
      "maxLength": 120,
      "description": "Jedinečný klíč požadavku (např. UUID). Opakování se stejným klíčem a stejnými argumenty vrátí původní výsledek a nic nezaloží znovu."
    }
  },
  "required": [
    "invoice_id",
    "amount",
    "paid_on",
    "client_request_id"
  ],
  "additionalProperties": false
}
```
