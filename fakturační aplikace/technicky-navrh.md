# Technický návrh – fakturační aplikace, 1. etapa

**Stav:** návrh k odsouhlasení
**Vychází z:** [`zadání/uvodni-zadani.md`](zadání/uvodni-zadani.md), vzor [`zadání/VF2657.PDF`](zadání/VF2657.PDF)
**Datum:** 13. 11. 2026

## 1. Rozhodnutí vlastníka (13. 11. 2026)

| # | Rozhodnutí | Dopad na návrh |
|---|---|---|
| R1 | V 1. etapě se vystavují **pouze faktury** (faktura – daňový doklad). | Nabídka, proforma, vyúčtovací faktura a opravný doklad jsou ve 2. etapě. Datový model s typem dokladu počítá. |
| R2 | Číselná řada je buď **celé číslo s nastavitelným startem**, nebo formát **`{YY}{N}`** (např. `2657` = rok 26, pořadí 57). | Kap. 5.4. |
| R3 | **Režimy a sazby DPH, bankovní účty a pravidlo variabilního symbolu** se nastavují v profilu fakturačního subjektu. | Kap. 4.2, 5.2, 5.5. Nic není napevno v kódu. |
| R4 | **PDF** zpočátku libovolné, vychází ze vzoru `VF2657.PDF`. | Jedna společná šablona, branding z profilu. |
| R5 | Pouze **globální správce** (`admin`), žádní správci jednoho profilu. | Otevřené rozhodnutí č. 3 uzavřeno. |
| R6 | **MCP server, zálohování/obnova přes UI a import historie** se přesouvají do 2. etapy. | Provozní zálohy databáze na serveru (kap. 8.3) zůstávají. |
| R7 | **Nejasnosti** ze zadání (storno vs. opravný doklad, daňový doklad k záloze, sdílení partnerů, retence…) se řeší ve 2. etapě. | V 1. etapě se volí nejjednodušší bezpečná varianta, kap. 10. |
| R8 | Test **nepotřebuje samostatnou Access aplikaci**; `faktury-test` zůstává pod Access aplikací „faktury“. | Odchylka od zadání kap. 1.4, případně později. Databáze, služba, tajné údaje a zálohy jsou oddělené už dnes. |

## 2. Rozsah 1. etapy

**Součástí:**
- přihlášení přes Cloudflare Access s ověřením JWT v aplikaci, lokální seznam uživatelů, oprávnění po profilech;
- správa fakturačních profilů (identita, DPH, bankovní účty, číselné řady, texty, logo) správcem;
- partneři (odběratelé) v rámci profilu;
- faktura: návrh, úprava, **náhled bez uložení**, vystavení s atomickým přidělením čísla, PDF s **QR Platbou**, storno s důvodem;
- ruční platby (částečné, přeplatek, protioperace), odvozený stav úhrady a „po splatnosti“;
- ruční evidence předání a upomínek, upozornění podle pravidel profilu;
- přehledy po profilech, souhrny v CZK, CSV export;
- audit, optimistický zámek, výrazné označení testovacího prostředí;
- produkční prostředí na stejném mechanismu nasazení jako test.

**Mimo 1. etapu:** MCP, zálohy a obnova přes UI, import historie, ostatní typy dokladů, e-maily, účetní exporty, bankovní integrace, cizí měny (viz zadání kap. 8).

## 3. Architektura

```
prohlížeč ──HTTPS──► Cloudflare Access (přihlášení, politika e-mailů)
                         │  Cf-Access-Jwt-Assertion
                         ▼
                   Cloudflare Tunnel (cloudflared v LXC 102)
                         │  http://localhost:8101 / 8102
                         ▼
          gunicorn + Django 5.2 (faktury-production / faktury-staging)
                         │
          PostgreSQL 17 (faktury_production / faktury_staging)
          soubory: /srv/apps/faktury-<env>/shared/media (PDF, loga)
```

- **Server-side rendering** v Django šablonách, minimum JavaScriptu; pro dynamické položky faktury a průběžné součty **htmx** (bez build kroku). Žádný SPA framework.
- **PDF:** WeasyPrint (HTML/CSS → PDF). Náhled i vystavené PDF používají **stejnou šablonu a stejný výpočetní kód**.
- **QR:** vlastní sestavení řetězce SPAYD, obrázek knihovnou `segno` (SVG vložené do PDF).
- **Peníze:** výhradně `Decimal`, v databázi `numeric`. Žádné `float`.
- **Čas:** v DB `timestamptz` (UTC), zobrazení `Europe/Prague`. Daňová data (vystavení, DUZP, splatnost) jsou typ `date`.
- **Infrastruktura:** stávající LXC 102. Instalační skript `setup-env.sh` doplní systémové knihovny pro WeasyPrint (Pango) a písma (DejaVu nebo Noto Sans s českou diakritikou).

### 3.1 Django aplikace (moduly)

| Modul | Obsah |
|---|---|
| `accounts` | vlastní model `User` (e-mail jako identita), `ProfileMembership` (oprávnění v profilu), middleware Cloudflare Access |
| `profiles` | `BillingProfile`, `BankAccount`, `VatRate`, `VatRegime`, `NumberSeries`, `ReminderRule` |
| `partners` | `Partner` |
| `invoicing` | `Invoice`, `InvoiceLine`, `Payment`, `DeliveryRecord`, `ReminderRecord`, výpočty, číslování, PDF, QR |
| `audit` | `AuditEvent`, pomocné funkce pro zápis |
| `reports` | přehledy, souhrny, CSV export |
| `core` | health check, banner prostředí, společné šablony, chybové stránky |

Business logika je ve **službách** (`services.py`), ne ve views. Views, budoucí MCP i management příkazy volají stejné služby a ty samy kontrolují oprávnění. Splní se tím požadavek „MCP není privilegovaná zkratka“ už teď.

## 4. Přihlášení a oprávnění

### 4.1 Ověření Cloudflare Access

Middleware `CloudflareAccessMiddleware` na **každém** požadavku kromě `/health/`:

1. přečte hlavičku `Cf-Access-Jwt-Assertion`; bez ní vrátí 403;
2. ověří podpis RS256 proti JWKS z `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`. Klíče se cachují a při neznámém `kid` znovu načtou;
3. ověří `aud` (AUD tag aplikace), `iss` (team doména), `exp` a `nbf` s tolerancí 60 s;
4. z claimu `email` vezme e-mail, ořízne ho a převede na malá písmena;
5. najde **aktivního** lokálního uživatele, jinak vrátí 403 se stránkou „Nemáte přístup“. Uživatel se nikdy nezakládá automaticky;
6. aktualizuje `last_login_at` (nejvýš jednou za 5 minut).

Konfigurace v `/etc/faktury/<env>.env`: `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`. Hlavička `Cf-Access-Authenticated-User-Email` se **nepoužívá**.

Fail-closed: chyba ověření nebo nedostupné JWKS (bez platné cache) znamená odmítnutí.

Ve vývoji a v testech lze middleware nahradit pevnou identitou jen při `APP_ENVIRONMENT=development` a v testech. Na stagingu a v produkci to konfigurace nedovolí, aplikace při startu odmítne nebezpečnou kombinaci nastavení.

### 4.2 Model oprávnění

- `User`: e-mail (unikátní, normalizovaný), jméno, `is_active`, `is_admin` (globální), `last_login_at`, auditní časy.
- `ProfileMembership(user, profile, can_view_invoices, can_modify_invoices, can_modify_partners)`.
  - `can_modify_invoices` implikuje čtení dokladů i partnerů profilu;
  - `can_modify_partners` implikuje čtení partnerů.
- `is_admin` zahrnuje vše ve všech profilech.

### 4.3 Vynucení

- Všechny adresy pracující s daty profilu mají profil **v URL**: `/p/<slug>/faktury/…`. Aktivní profil je tak vždy jednoznačný a zobrazený v hlavičce stránky.
- Dekorátor `@profile_permission("invoices:view")` načte profil a ověří oprávnění. Bez přístupu vrací 404, ne 403, aby neprozradil existenci profilu.
- Všechny dotazy jdou přes manager `for_profile(profile)`. Přímé `Invoice.objects.get(pk=…)` ve views zakáže test, který prochází zdrojový kód.
- **Automatický test oprávnění** projde všechny URL vzory a pro uživatele bez přístupu k profilu ověří odmítnutí čtení i zápisu (scénář 3).

## 5. Datový model

Zjednodušeně, bez auditních polí `created_at/by` a `updated_at/by`, která mají všechny editovatelné modely. Editovatelné modely mají `version` pro optimistický zámek.

### 5.1 BillingProfile

`slug`, `name`, `legal_form`, `street`, `city`, `zip`, `country`, `ico` (unikátní napříč všemi), `dic`, `vat_payer` (bool), `register_note` (text o zápisu v rejstříku), `email`, `phone`, `web`, `logo` (PNG/JPEG, max 1 MB), `is_active`, výchozí hodnoty:

- `default_due_days`, `default_payment_method`, `default_vat_regime`, `default_bank_account`;
- `constant_symbol` (např. `0308`, nepovinný);
- `vs_rule` (kap. 5.5);
- `rounding_mode`: `none` / `whole_czk`;
- `vat_calc_method`: `per_line` (výchozí) / `per_rate_total`;
- `invoice_footer_text` (např. „Úrok z prodlení činí 0,050 % za každý den prodlení platby.“), `invoice_intro_text` („Fakturujeme Vám“).

Shodné IČO nebo DIČ při zakládání vyvolá varování. U IČO je navíc unikátní constraint v databázi.

### 5.2 DPH

- `VatRate(profile, label, rate_percent, is_active, order)`: sazby, které profil nabízí (např. 21 %, 12 %, 0 %). Sazba se do položky faktury **kopíruje hodnotou**, takže změna nastavení neovlivní historii.
- `VatRegime(profile, code, label, applies_vat, invoice_text, is_active)`: režim celé faktury, např.:
  - „Tuzemské plnění“ (`applies_vat=true`),
  - „Přenesená daňová povinnost“ (`applies_vat=false`, text „Daň odvede zákazník“),
  - „Osvobozené plnění“,
  - „Neplátce DPH“.
  Text režimu se tiskne na fakturu. Konkrétní režimy profilů potvrdí účetní; aplikace je jen eviduje.

### 5.3 BankAccount

`profile`, `label`, `account_number` (tuzemský formát pro zobrazení), `iban` (validace kontrolní číslicí), `bic`, `bank_name`, `is_active`. QR Platba používá **výhradně** IBAN účtu zvoleného na faktuře, a ten musí patřit profilu faktury (constraint ve službě a test).

### 5.4 NumberSeries

`profile`, `document_type` (zatím jen `invoice`), `name`, `format`, `start_value`, `min_width` a interní tabulka čítačů `NumberCounter(series, period, last_value)`.

- `format = "{N}"`: průběžné celé číslo od `start_value`, `period = ''`.
- `format = "{YY}{N}"`: `period` = rok data vystavení; v novém roce se čítač založí na `start_value`, obvykle 1. Příklad: start 1, `min_width` 0, rok 2026, 57. faktura → `2657`. Při `min_width=3` → `26057`.
- Podporované tokeny: `{YYYY}`, `{YY}`, `{N}` a pevný text (prefix/oddělovač). Další tokeny se přidají jen podle potřeby.
- Start lze měnit volně jen do prvního vystavení v řadě (period). Potom jen správce, jen na vyšší nepoužitou hodnotu, s povinným důvodem v auditu.
- Přidělení čísla: `SELECT … FOR UPDATE` na řádek čítače v transakci vystavení. Unikátní constraint `(profile, series, number)` je pojistka proti duplicitě i při chybě logiky (scénáře 6 a 7).

> `{YY}` má vždy dvě číslice, takže se čísla různých let nepotkají ani bez pevné šířky pořadí. `min_width` ovlivňuje jen vzhled a řazení podle textu (`2657` vs. `26057`). U Martina Piecha se podle vzoru předpokládá `min_width=0`, potvrdí se dalšími doklady.

### 5.5 Variabilní symbol

`vs_rule` v profilu:
- `invoice_number_digits` (výchozí): VS = číslice z čísla faktury, max. 10 číslic, jinak chyba při vystavení;
- `manual`: VS zadá uživatel (jen číslice, max. 10).

### 5.6 Partner

V 1. etapě patří partner **právě jednomu profilu** (kap. 10, bod 3):

- `profile`, `name`, `ico`, `dic`, `vat_payer`, adresa, `country`, `billing_email`, `contact_person`, `phone`;
- `is_customer`, `is_supplier`, `is_active`, `internal_note`;
- `due_days_override`, `payment_method_override`.

Duplicitní IČO, DIČ nebo shodný název s adresou v rámci profilu vyvolá varování, nezablokuje uložení. Správce může partnera **zkopírovat** do jiného profilu.

### 5.7 Invoice a InvoiceLine

`Invoice`:
- `profile` (neměnný), `document_type='invoice'`, `status`: `draft` / `issued` / `cancelled`;
- `series`, `number` (null do vystavení), `variable_symbol`, `constant_symbol`, `specific_symbol`;
- `partner` (FK pro vyhledávání), `issue_date`, `taxable_supply_date` (DUZP), `due_date`;
- `payment_method` (`bank_transfer` / `cash` / `card` / `other`), `bank_account`, `vat_regime`;
- `public_note` (text pro odběratele), `internal_note`, `order_reference` (objednávka);
- součty: `total_base`, `total_vat`, `rounding`, `total`. U návrhu se přepočítávají, u vystavené faktury jsou zmrazené;
- **snímky** (JSON, zapisují se při vystavení): `issuer_snapshot` (identita a texty profilu), `partner_snapshot`, `bank_snapshot`, `vat_regime_snapshot`, `vat_summary` (rekapitulace po sazbách);
- `issued_at`, `issued_by`, `cancelled_at`, `cancelled_by`, `cancel_reason`;
- `pdf_file`, `pdf_sha256`, `data_hash` (hash kanonického JSON dat, ze kterých vzniklo PDF);
- `delivery_status`: `not_sent` / `delivered_outside`;
- `version`.

`InvoiceLine`: `invoice`, `position`, `description`, `quantity` (numeric 12,3), `unit`, `unit_price` (numeric 14,2, bez DPH), `discount_percent`, `vat_rate_percent` (kopie hodnoty), `line_base`, `line_vat`, `line_total`.

### 5.8 Platby, předání, upomínky

- `Payment(invoice, amount, paid_on, method, reference, note, reverses → Payment|null)`. Platba se nemaže ani needituje. Oprava je **protioperace** se zápornou částkou odkazující na původní platbu.
- `DeliveryRecord(invoice, delivered_at, by, note)`.
- `ReminderRule(profile, days_after_due, label)`.
- `ReminderRecord(invoice, reminded_on, by, method, result_note)`.

### 5.9 AuditEvent

`at`, `actor` (null u systémových akcí), `interface` (`web` / `mcp` / `system` / `cli`), `profile` (null u globálních akcí), `action` (např. `invoice.issue`), `object_type`, `object_id`, `before` (JSON), `after` (JSON), `request_id`.

Tabulka je **append-only**: databázový trigger odmítne `UPDATE` i `DELETE`, takže to neobejde ani chyba v aplikaci. Tajné údaje se do `before/after` nezapisují (whitelist polí).

## 6. Klíčové procesy

### 6.1 Výpočet faktury (jediná funkce pro návrh, náhled i vystavení)

Pro každou položku:
1. `line_base = round2(quantity × unit_price × (1 − discount_percent/100))`;
2. pokud režim počítá DPH: `line_vat = round2(line_base × rate/100)`, jinak 0;
3. `line_total = line_base + line_vat`.

Rekapitulace po sazbách:
- `per_line`: součet `line_base` a `line_vat` za sazbu;
- `per_rate_total`: součet `line_base` za sazbu, `vat = round2(base × rate/100)`. Řádkové DPH je pak jen informativní a rekapitulace je rozhodná.

Dál:
- `total = Σ base + Σ vat`;
- `rounding` podle `rounding_mode` (`whole_czk` = matematicky na celé Kč);
- `total_payable = total + rounding`.

`round2` = `ROUND_HALF_UP` na 2 desetinná místa.

Funkce je čistá (bez DB) a pokrytá tabulkovými testy včetně více sazeb a slev (scénář 5).

### 6.2 Náhled

`POST /p/<slug>/faktury/nahled/` s daty formuláře, nebo `GET …/<id>/nahled/` pro uložený návrh:
- sestaví fakturu **v paměti**, nic neukládá a nečerpá číslo;
- číslo zobrazí jako `NÁVRH`, celou stránku přeškrtne vodoznakem `NÁHLED – NENÍ VYSTAVENÝ DOKLAD`;
- výstup je PDF ze stejné šablony; v prohlížeči se zobrazí inline;
- QR kód se generuje stejně jako u vystavené faktury, z týchž hodnot;
- bez zvoleného profilu náhled neexistuje (profil je v URL).

### 6.3 Vystavení

Služba `issue_invoice(user, invoice_id, expected_version)` v jedné DB transakci:

1. zamkne fakturu (`FOR UPDATE`), ověří `status=draft`, `version`, oprávnění `invoices:modify`, aktivní profil, aktivního partnera, účet profilu, alespoň jednu položku a kladnou částku;
2. zamkne čítač řady a přidělí číslo (kap. 5.4);
3. určí VS (kap. 5.5);
4. vytvoří snímky a zmrazí součty a `data_hash`;
5. vygeneruje PDF do dočasného souboru, uloží ho pod `media/<profile>/invoices/<rok>/<číslo>-<sha8>.pdf` a spočítá `pdf_sha256`;
6. nastaví `status=issued`, `issued_at`, `issued_by` a zapíše audit;
7. commit. Když cokoli selže (i generování PDF), transakce se vrátí, číslo se nepřidělí a soubor se smaže.

Před potvrzením vystavení UI zobrazí rekapitulaci: **název profilu, IČO, bankovní účet, číselnou řadu a očekávané číslo** (scénář 4). Očekávané číslo je jen informativní; skutečné se přidělí až v kroku 2.

PDF vystavené faktury se po vystavení už **negeneruje znovu**. Stažení vrací uložený soubor (scénář 20). Neměnnost hlídá kromě služeb i DB trigger: u faktury se `status <> 'draft'` nelze měnit položky ani právně významná pole. Povolené změny jsou jen storno a stav předání.

### 6.4 Storno (prozatímní pravidlo do 2. etapy)

- Vystavenou fakturu lze stornovat s povinným důvodem, **jen pokud k ní není evidována platba** (součet plateb = 0).
- Číslo se znovu nepoužije, PDF zůstává, přehledy stornovanou fakturu vylučují ze souhrnů a zobrazují ji odlišně.
- Zda storno daňového dokladu stačí, nebo je nutný opravný daňový doklad, potvrdí ve 2. etapě účetní (zadání, otevřené rozhodnutí 8).

### 6.5 Stav úhrady (odvozený, neukládá se)

- `paid = Σ payments.amount`, `remaining = total_payable − paid`;
- stav:
  - `paid = 0` → neuhrazeno,
  - `0 < paid < total` → částečně uhrazeno,
  - `paid = total` → uhrazeno,
  - `paid > total` → přeplaceno;
- příznak „po splatnosti“: `remaining > 0` a `due_date < dnes (Europe/Prague)` (scénáře 13 a 14).

Počítá se v SQL anotaci, takže filtry a souhrny pracují bez denormalizace.

### 6.6 QR Platba

```
SPD*1.0*ACC:{IBAN}+{BIC}*AM:{total_payable:.2f}*CC:CZK*X-VS:{VS}*DT:{YYYYMMDD due}*MSG:Faktura {číslo}
```

Kódování a escapování podle specifikace qr-platba.cz. QR se negeneruje a místo něj se zobrazí upozornění, když:
- způsob úhrady ≠ převod,
- částka ≤ 0,
- chybí nebo je neplatný IBAN.

Test QR obrázek **dekóduje** (zbar) a porovná ho s údaji faktury (scénář 8).

### 6.7 Optimistický zámek

Každý formulář nese `version`. Uložení probíhá jako `UPDATE … WHERE id=? AND version=?` a při 0 změněných řádcích vrací konflikt. Uživatel uvidí hlášku s možností načíst aktuální data (scénář 16).

## 7. Uživatelské rozhraní

- **Hlavička:** výrazný název a IČO aktivního profilu, přepínač profilů (jen přístupné), uživatel.
- **Testovací prostředí:** oranžový pruh „TESTOVACÍ PROSTŘEDÍ – data nejsou ostrá“ na každé stránce, prefix `[TEST]` v titulku a vodoznak `TEST` v PDF ze stagingu (scénář 23).
- **Obrazovky:**
  - seznam faktur s filtry: číslo, partner, období vystavení a splatnosti, stav dokladu, předání a úhrady;
  - detail faktury: platby, předání, upomínky, audit;
  - formulář návrhu s dynamickými položkami a živými součty;
  - náhled;
  - potvrzení vystavení;
  - partneři;
  - přehled pro profil se souhrny vystaveno / uhrazeno / zbývá / po splatnosti;
  - administrace (uživatelé, profily, účty, sazby, režimy, řady, upomínková pravidla).
- **Globální administrátorský přehled** ukazuje profily vedle sebe, nikdy nesčítá jejich částky (scénář 15).
- **Upozornění na upomínky:** seznam faktur, u kterých nastal termín podle pravidel profilu a upomínka ještě nebyla zaznamenána.
- **CSV export** aktuálně filtrovaného seznamu: UTF-8 s BOM, středník jako oddělovač, aby se správně otevřel v českém Excelu.
- Formáty: datum `15.10.2026`, částky `1 936,00 Kč`.

## 8. Provoz, nasazení, testy

### 8.1 Prostředí

| | Test | Produkce |
|---|---|---|
| URL | faktury-test.vagasutca.org | faktury.vagasutca.org |
| služba / port | `faktury-staging` / 8102 | `faktury-production` / 8101 |
| DB | `faktury_staging` | `faktury_production` |
| nasazení | automaticky z `main` po zelených testech | z tagu `v*` po zelených testech |

**Převod produkce** (poslední krok 1. etapy):
1. `setup-env.sh production 8101 faktury.vagasutca.org`;
2. rozšíření sudoers o `faktury-deploy production *` a nový job `deploy-production` spouštěný tagem;
3. vypnutí `helloapp` a první nasazení.

Cesta v tunelu zůstává stejná (port 8101). Původní databázi `helloapp` lze po ověření smazat; nejsou v ní žádná data.

### 8.2 Testy

- **Jednotkové:** výpočty, formáty číselných řad, VS, SPAYD, validace IBAN, ověření JWT (s vlastním testovacím klíčem a JWKS).
- **Integrační:** proti PostgreSQL 17, služby a views, matice oprávnění přes všechny URL, neměnnost (triggery), souběžné vystavení ve dvou vláknech (scénář 7), optimistický zámek.
- **E2E:** Playwright proti `live_server` v CI, hlavní tok: partner → návrh → náhled → vystavení → PDF → platba → stav.
- **Kontrola na testovacím webu:** po každém nasazení projdu klíčové obrazovky v prohlížeči.
- CI navíc spouští `ruff` a `makemigrations --check`. Pokrytí služeb kolem 90 %, hlavně u výpočtů a oprávnění.

### 8.3 Provozní zálohy (minimum 1. etapy, bez UI)

- `pg_dump` před každým nasazením (už funguje);
- noční systemd timer: `pg_dump` + archiv `shared/media` do `/srv/apps/faktury-<env>/backups`, retence 30 dní;
- kopie mimo LXC (např. USB disk na Proxmoxu) a správa záloh v UI jsou ve 2. etapě.

### 8.4 Výchozí data

Management příkaz `bootstrap`:
- založí správce (e-mail zadaný parametrem);
- založí profily **F13 CyberTech s.r.o.** a **Martin Piech** s identifikačními údaji ze zadání (ARES 13. 11. 2026), výchozími sazbami 21/12/0 %, režimem „Tuzemské plnění“ a řadou `{YY}{N}`.

Bankovní účty se **nepřebírají automaticky**, ani z `VF2657.PDF`; doplní a potvrdí je správce v UI (zadání kap. 1.1).

## 9. Postup implementace (milníky 1. etapy)

Každý milník = sada commitů do `main` → automatické nasazení na test → kontrola → odsouhlasení vlastníkem.

| # | Milník | Akceptační scénáře |
|---|---|---|
| M1 | Základ: uživatelský model, JWT middleware, oprávnění, audit, banner testu, administrace uživatelů a profilů, `bootstrap` | 1, 2, 21 (část), 23 |
| M2 | Nastavení profilu: účty, sazby, režimy DPH, číselné řady, upomínková pravidla, logo | 2 |
| M3 | Partneři | 3 (část) |
| M4 | Návrh faktury, výpočty, náhled, PDF šablona podle vzoru, QR Platba | 4, 5, 8 |
| M5 | Vystavení, číslování, neměnnost, uložené PDF, storno | 6, 7, 9, 10, 20, 21 |
| M6 | Platby, stavy úhrady, předání, upomínky | 13, 14 |
| M7 | Přehledy, souhrny, CSV, globální přehled | 15 |
| M8 | Matice oprávnění, optimistický zámek všude, E2E, převod produkce, tag `v1.0.0` | 3, 16, 23 |

Scénáře 11, 12 (import), 17–19 (MCP), 22 a 24–27 (zálohy) patří do 2. etapy.

## 10. Prozatímní řešení nejasností (revize ve 2. etapě)

1. **Storno vystavené faktury:** změna stavu s důvodem, jen bez plateb (kap. 6.4). Opravný daňový doklad přijde ve 2. etapě.
2. **Daňový doklad k přijaté záloze:** 1. etapa zálohy neřeší; proforma a vyúčtování jsou ve 2. etapě. Účetní potvrdí, zda profily DDPP potřebují.
3. **Sdílení partnerů mezi profily:** v 1. etapě partner patří jednomu profilu, správce ho může zkopírovat. Odpadá tím riziko, že úprava v jednom profilu změní údaje v jiném, nebo že varování o duplicitě prozradí partnera cizího profilu.
4. **Výpočet DPH:** výchozí `per_line`, alternativa `per_rate_total` v nastavení profilu. Účetní potvrdí, co který profil používá.
5. **Retence a mazání:** v 1. etapě se faktury ani audit nemažou vůbec. Návrhy bez vazeb smazat lze.
6. **Samostatná Access aplikace pro test:** zatím ne (R8).
7. **MFA v Cloudflare Access:** mimo aplikaci, řeší se politikou v Cloudflare.

## 11. Rizika

| Riziko | Opatření |
|---|---|
| Nesprávné daňové náležitosti nebo výpočet | Konfigurovatelné režimy a texty, tabulkové testy výpočtů, kontrola PDF účetní před ostrým použitím |
| Systémové knihovny WeasyPrint na Debianu 13 | Ověřit v kontejneru s Debianem 13 už v M4, doplnit do `setup-env.sh` |
| 2 GB RAM pro 2 prostředí + runner + PostgreSQL | WeasyPrint jen při náhledu a vystavení, gunicorn 2 workery na prostředí; sledovat po M4 |
| Výpadek JWKS endpointu Cloudflare | Cache klíčů; bez platných klíčů fail-closed |
| Autonomní kontrola v prohlížeči závisí na běžící desktop aplikaci | Hlavní kontrola je Playwright v CI; prohlížeč je doplněk |
