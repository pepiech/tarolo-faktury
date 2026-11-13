# Zadání: fakturační aplikace pro více vystavujících subjektů

**Stav dokumentu:** pracovní specifikace k doplnění a schválení  
**Primární účel:** evidence, vystavení a sledování vydaných dokladů více samostatných podnikatelských subjektů v českém prostředí  
**Jazyk a časové pásmo:** čeština, `Europe/Prague`

## 1. Cíl a rozsah

Aplikace bude interním systémem pro více **fakturačních profilů**. Každý profil představuje samostatnou právnickou nebo fyzickou osobu podnikatele, která vystavuje vlastní doklady. Profily sdílejí jednu aplikaci, ale jejich identita, číslování, bankovní údaje, doklady, platby, importovaná historie a přehledy musí být striktně oddělené.

Evidence přijatých faktur ani dodavatelské účetní workflow nejsou součástí plánovaného rozsahu.

### 1.1 Známé fakturační profily

Údaje ověřené ve veřejném registru ARES dne 13. 11. 2026:

| Profil | Právní forma | IČO | DIČ / DPH | Sídlo |
| --- | --- | --- | --- | --- |
| **F13 CyberTech s.r.o.** | společnost s ručením omezeným (kód 112) | 06646654 | CZ06646654, aktivní plátce DPH | Sokola Tůmy 1099/1, Hulváky, 709 00 Ostrava |
| **Martin Piech** | fyzická osoba podnikající (kód 101) | 02963281 | CZ8007115424, aktivní plátce DPH | Gudrichova 469/82, Kylešovice, 747 06 Opava |

Zdroje: [ARES – F13 CyberTech s.r.o.](https://ares.gov.cz/ekonomicke-subjekty?ico=06646654) a [ARES – Martin Piech](https://ares.gov.cz/ekonomicke-subjekty?ico=02963281).

Další profil, například OSVČ vlastníka aplikace, může založit administrátor po doplnění jeho identifikačních a daňových údajů. Bankovní účet ani jiné provozní nastavení se nepřebírá automaticky z internetu a musí být potvrzeno odpovědnou osobou daného subjektu.

Soubor `VF2657.PDF` je vzorem a historickým dokladem profilu Martin Piech, nikoli společnosti F13. Jeho identita, číselná řada ani bankovní údaje se nesmí použít pro F13.

### 1.2 Rozsah první etapy

- více oddělených fakturačních profilů v jedné interní aplikaci; nejde o veřejnou multitenantní službu;
- administrátorská správa profilů a přidělování přístupu uživatelů k jednotlivým profilům;
- pouze vydané doklady v **CZK** a jejich úhrady;
- katalog partnerů, přičemž fakturační workflow první etapy používá odběratele;
- vytvoření a stažení PDF, náhled dokladu před uložením nebo vystavením a QR Platba;
- konfigurovatelné číselné řady včetně počáteční hodnoty;
- zpětný import dříve vydaných faktur pro doplnění historie;
- ruční evidence plateb, stavu předání dokladu a upomínek;
- přehledy a obecný CSV export seznamů;
- čtecí a zapisovací MCP rozhraní při zachování stejných oprávnění jako ve webové aplikaci.

Očekávaný rozsah dat je maximálně několik desítek partnerů a řádově stovky dokladů ročně napříč profily. Aplikace nemusí být navrhována pro vysoké objemy ani velký počet současně pracujících uživatelů.

### 1.3 Druhá etapa a možné budoucí rozšíření

- odesílání faktur a upomínek e-mailem včetně konfigurace odesílací adresy a šablon;
- účetní exporty, například formáty vyžadované konkrétním účetním systémem;
- případné napojení na banku pro import transakcí a automatickou aktualizaci stavů plateb.

Bankovní integrace zatím není závazným požadavkem; architektura pouze nemá bránit jejímu pozdějšímu doplnění.

### 1.4 Prostředí a adresy

- produkční prostředí: **`https://faktury.vagasutca.org`**;
- testovací prostředí: **`https://faktury-test.vagasutca.org`**.

Obě prostředí musí být chráněná přes Cloudflare Access, mít samostatně konfigurovanou Access aplikaci/politiku a používat oddělenou databázi, úložiště dokumentů, konfiguraci, tajné údaje a zálohy. Testovací prostředí musí být v UI výrazně označeno, aby nemohlo dojít k záměně s produkcí. Nasazení nové verze a obnova zálohy se nejprve ověřují v testovacím prostředí. Produkční data se do testu nekopírují automaticky; pokud administrátor nahraje produkční zálohu do testu, platí pro test stejné požadavky na ochranu dat jako pro produkci.

Položky označené v kapitole **Otevřená rozhodnutí** je nutné uzavřít před implementací příslušné části.

## 2. Pojmy

- **Fakturační profil / vystavující subjekt** – samostatná právnická nebo fyzická osoba podnikatele, jejíž identifikační, daňové, bankovní a kontaktní údaje se tisknou na doklady.
- **Partner** – odběratel nebo dodavatel evidovaný v adresáři. Partner může mít oba příznaky současně.
- **Doklad** – společné označení pro nabídku, proformu, fakturu, vyúčtování a opravný doklad. Jednotlivé typy nemají totožné právní účinky ani životní cyklus.
- **Oprávnění** – kombinovatelné příznaky určující, co uživatel smí dělat. Nejde o navzájem výlučné role.

## 3. Přihlášení a uživatelé

### 3.1 Přihlášení

- Přihlášení zajišťuje **Cloudflare Access**. Aplikace nespravuje hesla a nemá funkci „zapomenuté heslo“.
- Aplikace převezme ověřenou identitu uživatele (minimálně e-mail) pouze z důvěryhodného požadavku od Cloudflare Access.
- Přímý přístup k aplikačnímu serveru mimo Cloudflare musí být síťově zakázán. Nestačí důvěřovat samotné hlavičce s e-mailem, protože by ji jinak mohl klient podvrhnout.
- E-mail se před porovnáním normalizuje (oříznutí mezer a porovnání bez ohledu na velikost písmen).
- Uživatel musí existovat v lokálním seznamu a být aktivní. Neznámý ani deaktivovaný e-mail nezíská přístup a nevytvoří se automaticky.
- Způsob získání identity a ověření podpisu/tokenů Cloudflare bude popsán v technickém návrhu.

### 3.2 Správa uživatelů

U uživatele se eviduje minimálně e-mail, zobrazované jméno, stav aktivní/neaktivní, přístupné fakturační profily, oprávnění v každém profilu, datum posledního přihlášení a auditní údaje o vytvoření a změnách.

Oprávnění:

| Oprávnění | Význam |
| --- | --- |
| `admin` | Globální správa uživatelů a fakturačních profilů, přidělování přístupů a plný přístup ke všem funkcím. |
| `invoices:modify` | Vytváření a úprava návrhů, vystavení dokladů a práce s platbami v přiděleném profilu. Zahrnuje čtení dokladů a partnerů tohoto profilu. |
| `invoices:view` | Čtení dokladů, plateb a přehledů přiděleného profilu bez možnosti změny. |
| `partners:modify` | Vytváření, úprava a přiřazování partnerů v přiděleném profilu. Zahrnuje jejich čtení. |

Pravidla:

- oprávnění lze kombinovat;
- oprávnění mimo `admin` se přidělují samostatně pro každý fakturační profil;
- `admin` je globální oprávnění a zahrnuje přístup ke všem profilům i všechna ostatní oprávnění;
- uživatel nesmí číst ani měnit doklady, platby, nastavení nebo přehledy profilu, ke kterému nemá přístup;
- deaktivace uživatele zablokuje přístup bez odstranění jeho auditní historie;
- změna profilu, přístupů nebo oprávnění, vystavení či stornování dokladu a ruční změna platby se vždy auditují.

## 4. Funkční moduly

### 4.1 Administrátorská správa fakturačních profilů

Administrátor může profil vytvořit, upravit, deaktivovat a přidělit k němu uživatele. Profil s existujícím dokladem nelze fyzicky odstranit; lze jej pouze deaktivovat pro nové vystavování. Deaktivace neomezí oprávněné čtení historie.

U každého profilu se evidují minimálně:

- obchodní firma/název, právní forma, sídlo, IČO, DIČ a stav plátce DPH;
- kontaktní údaje;
- bankovní účty pro platby v CZK, včetně IBAN a případně BIC/SWIFT;
- výchozí splatnost, způsob platby, vlastní číselné řady, jejich počáteční hodnoty a texty na dokladech;
- logo a údaje potřebné pro generované PDF;
- pravidla zaokrouhlení a aktuálně používané sazby DPH jako konfigurovatelné hodnoty, nikoli napevno zapsané konstanty.

IČO je jedinečné napříč všemi profily včetně deaktivovaných. Aplikace musí před založením nového profilu upozornit na shodné IČO nebo DIČ.

Administrátor nastaví, které typy dokladů smí profil vystavovat, výchozí DPH režim, číselné řady, účet pro QR Platbu a oprávněné uživatele. Změna nastavení nesmí zpětně změnit již vystavené doklady.

V uživatelském rozhraní je vždy jasně vidět aktivní profil. Před vystavením aplikace zobrazí jeho název, IČO, bankovní účet a číselnou řadu. Již vystavený ani importovaný doklad nelze přesunout do jiného profilu.

### 4.2 Partneři

U partnera se eviduje minimálně:

- název/jméno, IČO, DIČ, stav plátce DPH;
- sídlo/fakturační adresa, země a kontaktní osoby;
- e-mail pro fakturaci;
- příznak odběratel/dodavatel, stav aktivní/neaktivní a interní poznámka;
- výchozí splatnost a způsob platby, pokud se liší od nastavení fakturačního profilu.

IČO ani DIČ nemusí být vždy vyplněno (například u některých nepodnikajících fyzických osob nebo zahraničních subjektů). Duplicitní IČO/DIČ nebo shodný název a adresa mají vyvolat upozornění, nikoli bezpodmínečné zamítnutí.

Základní identita partnera může být sdílená, aby se stejný odběratel nezadával opakovaně. Partner se ale výslovně přiřazuje k jednomu nebo více fakturačním profilům. Uživatel jej vidí pouze v profilech, ke kterým má přístup. Výchozí splatnost, způsob platby, interní poznámka a případné zákaznické označení mohou být pro každý profil jiné.

Doklad uchovává **snímek údajů vystavujícího profilu i partnera platný v okamžiku vystavení**. Pozdější změna profilu nebo partnera tedy nezmění historické PDF ani údaje vystaveného dokladu.

### 4.3 Typy dokladů

| Typ | Účel a zvláštní pravidla |
| --- | --- |
| Cenová nabídka | Není fakturou ani daňovým dokladem. Má vlastní platnost a může být přijata, odmítnuta nebo převedena na další doklad. |
| Proforma / zálohová výzva | Výzva k úhradě; sama o sobě není daňovým dokladem. Musí být možné navázat přijatou platbu a následný doklad. |
| Faktura | Řádný vydaný doklad se splatností a stavem úhrady. |
| Vyúčtovací faktura | Zohlední navázané zálohy a jednoznačně ukáže původní částku, zaplacené zálohy a doplatek/přeplatek. |
| Opravný daňový doklad („dobropis“) | Vždy odkazuje na původní doklad a uvádí důvod a opravované částky. Není pouhým záporným přepsáním původní faktury. |

Každý typ může mít v každém fakturačním profilu vlastní číselnou řadu, šablonu PDF a povolené přechody stavů.

### 4.4 Údaje dokladu a položek

Doklad obsahuje podle svého typu minimálně:

- neměnný odkaz na fakturační profil, typ, interní identifikátor a po vystavení jedinečné evidenční číslo;
- snímek identifikačních, daňových a bankovních údajů vystavujícího profilu a snímek partnera;
- datum vystavení, datum uskutečnění zdanitelného plnění nebo datum přijetí úplaty, datum splatnosti a případně dobu platnosti nabídky;
- měnu `CZK`; jiné měny ani směnné kurzy se v první etapě nepodporují;
- způsob platby, bankovní účet, variabilní symbol a případně konstantní/specifický symbol;
- vazby na zdrojový, zálohový nebo opravovaný doklad;
- interní poznámku, veřejný text pro odběratele a přílohy;
- položky: popis plnění, množství, měrnou jednotku, jednotkovou cenu, slevu, sazbu/režim DPH a výsledné částky;
- součty podle sazeb DPH, základ daně, DPH, celkem, uhrazeno a zbývá uhradit.

Výpočty musí být deterministické a mít jednotně definované pořadí výpočtu a zaokrouhlení. U daňových dokladů se musí zohlednit zákonné náležitosti podle konkrétního daňového režimu; nejde jen o univerzální seznam polí. Výše DPH se na daňovém dokladu uvádí v české měně.

### 4.5 Číslování a neměnnost

- Každá číselná řada patří právě jednomu fakturačnímu profilu a typu dokladu. Má konfigurovatelný formát a **počáteční číselnou hodnotu**. Formát může obsahovat například rok, pevný prefix a číselnou část.
- Počáteční nebo následující hodnotu lze volně nastavit pouze před prvním vystavením v dané řadě. Pozdější změna je operace správce, smí vést jen na dosud nepoužitou vyšší hodnotu a musí obsahovat důvod v auditu.
- Číslo se přidělí atomicky až při vystavení dokladu, nikoli při vytvoření návrhu.
- Číslo je jedinečné v kombinaci fakturační profil + číselná řada + číslo. Stejné číslo může existovat u dvou různých vystavujících subjektů, nikdy však dvakrát v téže řadě. Po zrušení či stornu se znovu nepoužije.
- Vystavení kontroluje kolizi s běžnými i importovanými historickými doklady stejného profilu. Nejvyšší importované číslo samo automaticky neposouvá řadu bez potvrzení správce, ale aplikace nesmí dovolit vznik duplicity.
- Návrh lze upravovat a odstranit, pokud na něj nejsou navázané další záznamy.
- Po vystavení se údaje s právním nebo účetním významem neupravují přepsáním. Oprava se provede stornem nebo navázaným opravným dokladem podle povahy chyby.
- U vystaveného dokladu se uchová verze dat, z níž bylo vygenerováno PDF, čas vystavení a uživatel, který akci provedl.

### 4.6 Náhled dokladu

Náhled je požadovaná funkce první etapy. Je užitečný pro kontrolu rozvržení, výpočtů, platebních údajů a QR kódu ještě před uložením nebo vystavením dokladu.

- Náhled lze vytvořit přímo z právě vyplněných údajů bez založení záznamu v databázi.
- Náhled vždy výrazně zobrazí vybraný fakturační profil, jeho IČO a bankovní účet. Bez zvoleného profilu jej nelze vytvořit.
- Náhled nepřiděluje ani nerezervuje číslo z ostré číselné řady, nemění stav a nevytváří doklad.
- Místo ostrého čísla zobrazí text `NÁVRH` a musí být výrazně označen `NÁHLED – NENÍ VYSTAVENÝ DOKLAD`.
- Náhled používá stejné výpočty a stejnou šablonu jako výsledné PDF, aby věrně odhalil chyby před vystavením.
- Neuložená data náhledu jsou dočasná. Pokud je chce uživatel zachovat, musí je uložit jako návrh.
- Je-li možné vygenerovat QR Platbu, zobrazí se i v náhledu a musí odpovídat zobrazeným platebním údajům.

### 4.7 Import historických vydaných faktur

Import slouží k doplnění historie dokladů vystavených jednotlivými profily před spuštěním aplikace. Nejde o nové vystavení faktury.

- Importovat lze pouze dříve vydané doklady vybraného profilu, nikoli přijaté faktury.
- Import může spustit pouze správce (`admin`), protože ovlivňuje historii a kontrolu číselných řad.
- Před potvrzením importu administrátor vybere profil a aplikace porovná IČO/DIČ vystavitele z importovaných dat s profilem. Neshoda import zablokuje. Doklad nelze po importu převést pod jiný profil.
- Faktury odpovídající vzoru `VF2657.PDF` se importují pod profil Martin Piech a nesmí ovlivnit řady ani přehledy F13.
- Importovaný doklad si zachová původní číslo, data, odběratele, položky nebo alespoň souhrnné částky, stav úhrady a původní PDF, je-li dostupné. Pokud nejsou importovány jednotlivé položky, je původní PDF povinné a aplikace musí přiznat, že detail položek není strukturovaně dostupný.
- Každý importovaný záznam je označen jako `imported`, obsahuje zdroj importu, čas importu a uživatele, který jej provedl.
- Import nepřidělí nové číslo, neodešle doklad, nespustí upomínku a nevytvoří novou účetní událost mimo samotný audit importu.
- Před potvrzením importu proběhne náhled a validace. Kontrolují se minimálně duplicity čísel, povinné údaje, logika dat, součty a případné kolize s číselnou řadou.
- Importované doklady jsou po potvrzení historicky neměnné stejně jako běžně vystavené doklady. Opravit lze pouze administrativní metadata importu se zachováním auditu.
- Jednotlivý doklad lze doplnit formulářem. Pro větší množství musí být k dispozici hromadný import z předem definovaného CSV s mapováním a chybovým protokolem; konkrétní sloupce se stanoví podle dostupných historických dat.
- Importované doklady se zahrnují do vyhledávání, historie partnera, přehledů a MCP dotazů. Výstup musí rozlišit importovaný doklad od dokladu vystaveného aplikací.

### 4.8 Stavy

Původní jediný seznam stavů se rozdělí do samostatných os, aby se nemíchal životní cyklus dokladu s platbou a komunikací.

**Stav dokladu:**

- návrh;
- vystaveno;
- zrušeno/stornováno (pouze s důvodem a auditním záznamem).

**Stav předání v první etapě:**

- neodesláno;
- označeno jako předané/odeslané mimo aplikaci, včetně data a uživatele.

Automatické odesílání e-mailem a technické stavy doručení budou doplněny až ve druhé etapě.

**Stav úhrady (odvozený z plateb a splatnosti):**

- neuhrazeno;
- částečně uhrazeno;
- uhrazeno;
- přeplaceno;
- po splatnosti.

**Stav upomínek:** eviduje datum a výsledek každé upomínky; není nahrazován jednou hodnotou „1. / 2. upomínka“.

Nabídka může mít navíc stav platnosti (aktivní, přijatá, odmítnutá, expirovaná). Přechody mezi stavy a oprávnění k nim musí být před implementací popsány stavovým diagramem nebo tabulkou.

### 4.9 Platby a upomínky

- K dokladu lze zapsat jednu či více plateb v CZK s částkou, datem, způsobem, referencí a poznámkou.
- Aplikace podporuje částečnou úhradu, přeplatek a zrušení chybně zadané platby auditovanou protioperací.
- Stav „po splatnosti“ vzniká automaticky z neuhrazené částky a data splatnosti.
- Upomínková pravidla se konfigurují podle počtu dnů po splatnosti. V první etapě aplikace pouze upozorní uživatele a umožní ručně zaznamenat provedenou upomínku.
- Automatické odesílání upomínek e-mailem patří do druhé etapy.
- Automatické párování bankovních transakcí není součástí první etapy. Pozdější bankovní integrace může doplnit platby a jejich zdroj, ale nesmí bez auditní stopy přepsat ruční záznam.

### 4.10 QR Platba

- Faktura se způsobem úhrady bankovním převodem obsahuje QR kód podle standardu **QR Platba / SPAYD** pro tuzemskou platbu v CZK.
- Účet a identita příjemce se vždy převezmou z vybraného fakturačního profilu. Aplikace nesmí použít účet posledního dokladu nebo jiného profilu.
- QR kód obsahuje minimálně účet příjemce ve formátu IBAN, částku, měnu `CZK`, variabilní symbol a podle možností datum splatnosti a stručnou zprávu pro příjemce.
- Obsah QR kódu musí přesně odpovídat platebním údajům uvedeným čitelně na dokladu. Generování používá stejné zdrojové hodnoty jako PDF, nikoli ručně sestavenou kopii dat.
- QR kód se nevygeneruje, pokud chybí nebo je neplatný účet, částka není kladná nebo způsob platby není bankovní převod. Uživatel místo chybného kódu dostane srozumitelné upozornění.
- U vystaveného PDF odpovídá QR kód částce k úhradě v okamžiku vystavení. Pozdější částečná platba nesmí změnit archivované PDF.
- QR kód je součástí náhledu a musí být dostatečně velký a kontrastní pro běžné bankovní aplikace.
- Referenční standard: [QR Platba – specifikace formátu](https://qr-platba.cz/pro-vyvojare/specifikace-formatu/).

### 4.11 PDF, předání a přehledy

- Z vystaveného dokladu lze vytvořit reprodukovatelné PDF v češtině se všemi povinnými a sjednanými údaji a brandingem vybraného profilu.
- V první etapě lze PDF stáhnout a ručně označit jako předané nebo odeslané mimo aplikaci. Zaznamená se čas, uživatel a volitelná poznámka.
- E-mailové odesílání, odesílací adresa, šablony a sledování chyb doručení patří do druhé etapy.
- Opětovné stažení nebo označení předání již vystaveného dokladu nevytvoří nové číslo ani novou fakturu.
- Přehledy jsou vždy omezené na jeden zvolený profil a umožní filtrovat minimálně podle typu, čísla, partnera, období vystavení, splatnosti, stavu dokladu, předání a úhrady.
- Souhrny v CZK ukážou vystaveno, uhrazeno, zbývá uhradit a po splatnosti odděleně pro každý profil. Globální administrátorský přehled může zobrazit profily vedle sebe, ale nesmí jejich částky sloučit do jednoho obratu.
- Seznamy lze exportovat do obecného CSV. Specializované účetní exporty patří do druhé etapy.

## 5. MCP server

MCP server je další rozhraní nad stejnými daty a pravidly, nikoli privilegovaná zkratka mimo aplikaci.

Primární použití je rychlá práce přes AI, například:

- „Už mi zaplatila firma XY poslední fakturu vystavenou za F13?“
- „Které faktury Martina Piecha jsou po splatnosti?“
- „Vytvoř za moje OSVČ fakturu pro ABC za položku C s částkou X a splatností Y.“

Požadované skupiny nástrojů:

- vyhledání a načtení partnera nebo dokladu, včetně poslední vydané faktury partnera;
- zjištění stavu dokladu, úhrady, zdroje a času poslední aktualizace platby, splatnosti a přehledových součtů;
- vytvoření a úprava **návrhu** dokladu;
- vygenerování náhledu návrhu;
- samostatné operace pro vystavení, storno a zápis platby.

Pravidla MCP:

- každé volání je autentizované, auditované a autorizované vůči konkrétnímu profilu; čtení vyžaduje v daném profilu nejméně `invoices:view`, vytváření návrhu a další změny `invoices:modify`;
- každý výsledek obsahuje identifikátor a název vystavujícího profilu;
- zapisovací operace vždy vyžadují explicitní profil. MCP nesmí použít naposledy zvolený profil ani profil odhadnout z partnera;
- pokud uživatel profil neuvede a má přístup k více profilům, MCP se zeptá, za koho má dotaz nebo změnu provést;
- čtecí dotaz bez profilu může hledat pouze v uživateli přístupných profilech a výsledek musí rozdělit podle profilu. Pokud má stejný partner doklady ve více profilech, nástroj vyžádá upřesnění;
- nástroje vracejí stabilní identifikátory, strukturované chyby a jednoznačně rozlišují návrh od vystaveného dokladu;
- zapisovací operace přijímají idempotency key / `client_request_id`, aby opakování požadavku nevytvořilo duplicitní doklad nebo platbu;
- běžný pokyn „vytvoř fakturu“ po určení profilu vytvoří pouze návrh a vrátí jeho rekapitulaci včetně vystavitele a odkaz/identifikátor pro kontrolu nebo náhled;
- vystavení nebo storno musí být samostatná operace s výslovným záměrem volajícího a rekapitulací výsledku;
- odpověď na dotaz o zaplacení uvede vystavující profil, partnera, číslo a datum faktury, celkovou a zbývající částku, splatnost, stav úhrady a čas/zdroj poslední aktualizace; nesmí pouze odpovědět „ano/ne“ bez identifikace podkladu;
- pokud dotaz odpovídá více partnerům nebo dokladům, nástroj nesmí svévolně vybrat jeden záznam a musí vyžádat upřesnění;
- vyhledávání je stránkované a omezené; citlivá data se nevracejí bez funkční potřeby;
- MCP nesmí umožnit přepis vystaveného dokladu ani obcházet auditní stopu.

Konkrétní schéma nástrojů bude samostatně popsáno a verzováno.

## 6. Audit, bezpečnost a provoz

- Auditní záznam obsahuje fakturační profil, kdo, kdy, co a z jakého rozhraní provedl, včetně původní a nové hodnoty u významných změn.
- Auditní log běžný uživatel nemůže měnit ani mazat.
- Omezení přístupu podle profilu se vynucuje na serveru u každého čtení i zápisu; nestačí pouze skrýt profily v uživatelském rozhraní.
- Založení, změna, deaktivace profilu a změna přidělených uživatelů se vždy auditují.
- Veškerá komunikace probíhá šifrovaně; tajné údaje se neukládají do zdrojového kódu ani auditních zpráv.
- Přílohy mají omezení typu a velikosti a nesmí být přímo spustitelné.
- U editovatelných záznamů se používá optimistická kontrola souběhu pomocí verze záznamu nebo ekvivalentního hashe. Při uložení se ověří, že se záznam od načtení nezměnil; při konfliktu se změna odmítne a uživatel dostane možnost znovu načíst aktuální data. Nikdy nesmí dojít k tichému přepsání novější změny.
- Zálohy databáze a dokumentů se provádějí automaticky i ručně; obnova se musí periodicky ověřovat. Konkrétní RPO/RTO se doplní.
- Je definována retenční politika pro doklady, přílohy a audit. Po doplnění e-mailů ve druhé etapě se politika rozšíří také na zprávy a doručovací události. Mazání musí respektovat účetní a daňové povinnosti a před nasazením je potvrdí odpovědná účetní/daňová osoba.
- Aplikace používá české formátování data a částek, ale ukládání času musí být jednoznačné a odolné vůči změnám letního času.
- Chyba generování PDF, importu nebo jiné integrace nesmí způsobit ztrátu či částečné uložení vystaveného dokladu.

### 6.1 Administrátorská správa záloh a obnova

Správa záloh je dostupná pouze uživateli s globálním oprávněním `admin`. Běžný uživatel nesmí zálohy zobrazit, vytvořit, stáhnout, nahrát, odstranit ani obnovit.

Administrátor může:

- zobrazit seznam dostupných záloh včetně prostředí, času vytvoření, typu (automatická/ruční/nahraná), verze aplikace a schématu, velikosti, kontrolního součtu, stavu a autora;
- spustit ruční vytvoření úplné zálohy;
- stáhnout vybranou zálohu jako jeden přenositelný balíček;
- nahrát záložní balíček z lokálního počítače;
- zobrazit výsledek validace a obsahový souhrn nahrané zálohy;
- spustit obnovu až jako samostatný krok po úspěšné validaci a výslovném potvrzení;
- spravovat retenční lhůty a po potvrzení odstranit zálohu, pokud tím neporuší minimální retenční politiku.

Úplná záloha obsahuje minimálně databázi, fakturační profily, uživatele a oprávnění, partnery, doklady a jejich neměnné snímky, platby, auditní vazby, importovanou historii, vygenerovaná PDF a přílohy a konfigurovatelné šablony. Neobsahuje provozní tajné údaje, hesla, Cloudflare přihlašovací údaje ani jiné klíče, které mají být spravovány samostatně.

Pravidla bezpečné obnovy:

- samotný upload nikdy automaticky nespustí obnovu;
- před přijetím se kontroluje formát, kontrolní součet, úplnost, podporovaná verze a kompatibilita databázového schématu; poškozený nebo neznámý balíček se odmítne bez změny dat;
- před obnovou se zobrazí prostředí původu, čas zálohy, počty profilů, uživatelů, dokladů a příloh a upozornění, která data budou nahrazena;
- před každou obnovou se automaticky vytvoří bezpečnostní záloha aktuálního stavu;
- obnova je atomická: buď se dokončí celá, nebo se aplikace vrátí do původního stavu;
- během obnovy aplikace nepovolí běžné zápisy a po dokončení provede kontrolu integrity;
- produkční záloha se má nejprve zkušebně obnovit a ověřit na `faktury-test.vagasutca.org`;
- vytvoření, stažení, upload, odstranění a obnova zálohy se auditují včetně uživatele, času, prostředí a výsledku;
- záložní balíček musí být při přenosu chráněn HTTPS a při uložení šifrován. Způsob šifrování přenositelného staženého balíčku a správa obnovovacího tajemství jsou otevřené technické rozhodnutí.

Ruční zálohy nenahrazují automatický plán. Automatické zálohování má používat oddělené úložiště od běžných dat aplikace a pravidelně testovanou obnovu.

## 7. Minimální akceptační scénáře

První verze je přijatelná, pokud lze prokázat alespoň následující scénáře:

1. Známý aktivní uživatel projde přes Cloudflare Access; neznámý nebo deaktivovaný uživatel přístup nezíská.
2. Administrátor založí nový profil OSVČ, nastaví jeho identitu, účet a číselnou řadu a přidělí k němu vybraného uživatele.
3. Uživatel bez přístupu k profilu nemůže číst ani změnit jeho partnera, doklad, platbu, nastavení nebo souhrny, a to ani přes MCP nebo přímý požadavek na server.
4. Aktivní profil je v UI vždy zřetelný a před vystavením se zobrazí jeho název, IČO, účet a použitá číselná řada.
5. Uživatel vyplní fakturu s více sazbami DPH a zobrazí věrný náhled se správným vystavitelem bez uložení, bez spotřebování čísla a s označením, že nejde o vystavený doklad.
6. Správce nastaví každému profilu vlastní počáteční hodnotu číselné řady a první vystavené doklady obdrží očekávaná čísla. Shodné číslo ve dvou profilech je povoleno, duplicita v jedné řadě nikoli.
7. Dvě souběžná vystavení ve stejné řadě nevytvoří duplicitní číslo.
8. QR kód v náhledu a PDF použije účet vybraného profilu, projde validací SPAYD a obsahuje stejnou částku v CZK a variabilní symbol jako doklad.
9. Vystavený ani importovaný doklad nelze přesunout pod jiný profil, potichu přepsat nebo odstranit; oprava zanechá dohledatelnou vazbu a auditní stopu.
10. Změna adresy, účtu nebo jiných údajů profilu či partnera nezmění historický vystavený doklad.
11. Vzor `VF2657.PDF` lze importovat pod profil Martin Piech s původním číslem a PDF, ale import pod F13 je kvůli neshodě vystavitele odmítnut.
12. Import nevytvoří nové číslo ani odeslání a je ve výstupech označen jako importovaný; duplicitní nebo nekonzistentní import se odmítne s konkrétním chybovým protokolem.
13. Částečná a následná úplná platba správně změní odvozený stav a zbývající částku pouze v příslušném profilu.
14. Neuhrazený doklad se po datu splatnosti zobrazí jako „po splatnosti“ bez ruční změny stavu.
15. Souhrny a obraty F13, Martina Piecha a další OSVČ zůstanou oddělené.
16. Pokus uložit starší verzi záznamu po změně jiným uživatelem skončí konfliktem, nikoli přepsáním novějších dat.
17. MCP pokyn bez určení vystavitele vyžádá upřesnění, pokud má uživatel přístup k více profilům.
18. MCP dotaz na poslední fakturu partnera vrátí vystavující profil, jednoznačný doklad, stav a zdroj/čas aktualizace platby; pokud má partner doklady ve více profilech, vyžádá upřesnění nebo výsledky bezpečně rozdělí.
19. Opakování stejného zapisovacího MCP požadavku se stejným `client_request_id` nevytvoří duplicitu a pokyn „vytvoř fakturu“ založí pouze návrh ve výslovně zvoleném profilu.
20. PDF obsahuje správnou identitu a branding profilu a jeho opětovné stažení nebo označení předání nevytvoří nový doklad.
21. Deaktivovaný profil nelze použít pro nový doklad, ale oprávněný uživatel může číst jeho historii.
22. Záloha se dá obnovit v testovacím prostředí včetně profilů, přístupů, dokladů, plateb, importu a auditu.
23. Produkce běží na `faktury.vagasutca.org`, test na `faktury-test.vagasutca.org`; test je viditelně označený a prostředí nesdílejí databázi, dokumentové úložiště, tajné údaje ani zálohy.
24. Běžný uživatel nemůže zobrazit ani použít správu záloh; administrátor vytvoří ruční zálohu, vidí její stav a kontrolní součet a stáhne ji jako jeden balíček.
25. Upload poškozené, neúplné nebo nekompatibilní zálohy skončí validační chybou a nezmění žádná data.
26. Nahrání platné zálohy samo obnovu nespustí; administrátor nejprve vidí její souhrn a obnovu samostatně potvrdí.
27. Před obnovou vznikne bezpečnostní záloha aktuálního stavu; úspěšná obnova v testovacím prostředí zachová vazby a projde kontrolou integrity.

## 8. Mimo výchozí rozsah

Dokud nebude rozhodnuto jinak, nejsou součástí první verze:

- přijaté faktury a schvalovací workflow;
- podvojné účetnictví, sklad, mzdy, daňová přiznání a podání na úřady;
- cizí měny a směnné kurzy;
- přímé odesílání faktur a upomínek e-mailem včetně správy odesílací adresy (druhá etapa);
- specializované účetní exporty včetně ISDOC/EDI/PEPPOL (druhá etapa);
- automatický import a párování bankovních transakcí;
- veřejná samoobslužná multitenantní služba, registrace cizích organizací a jejich oddělené zákaznické účty;
- samoobslužná registrace uživatelů a lokální hesla;
- cizojazyčné šablony;
- automatické načítání dat z ARES/VIES;
- pravidelně se opakující fakturace.

Tyto body nejsou zamítnuté požadavky; jde o ochranu rozsahu první verze.

## 9. Otevřená rozhodnutí před implementací

1. Které typy dokladů jsou nutné už v první etapě a budou povolené shodně pro všechny profily?
2. Jaké jsou identifikační, daňové, kontaktní a bankovní údaje dalšího profilu OSVČ a kdo je před nasazením potvrdí?
3. Mají existovat pouze globální administrátoři, nebo také správci omezení na jeden profil?
4. Jaký bude přesný formát číselných řad každého profilu, jejich počáteční hodnoty, oddělení podle typu a pravidlo změny roku? U Martina Piecha je formát čísla `2657` pouze hypotézou `rok 26 + pořadí 57`, dokud jej nepotvrdí další doklady nebo export z Premieru.
5. Jaké sazby a režimy DPH používá každý profil (tuzemsko, osvobození, reverse charge, EU/třetí země apod.)?
6. Které interně potvrzené bankovní účty použijí jednotlivé profily pro faktury a QR Platbu a jak každý profil tvoří variabilní symbol?
7. V jaké podobě jsou dostupné historické faktury jednotlivých profilů (export z Premieru, CSV, jiné tabulky, jednotlivá PDF) a jaké mapování bude potřeba?
8. Jaká jsou pravidla storna a opravy, retenční lhůty a kdo je schválí z účetního/daňového pohledu pro každý profil?
9. Jaký přesný údaj předává Cloudflare Access, jak se ověřuje a je na úrovni politiky vyžadováno MFA?
10. Jak se bude MCP klient autentizovat vůči Cloudflare/aplikaci a zda má smět po samostatném potvrzení také vystavovat doklady, nebo pouze připravovat návrhy?
11. Jak mají vypadat PDF šablony jednotlivých profilů, která loga a povinné/dobrovolné texty mají obsahovat? Má se pro Martina Piecha vzhledově zachovat vzor `VF2657.PDF`?
12. Jaké jsou požadované parametry zálohování a obnovy: frekvence automatických záloh, minimální retence, RPO/RTO, maximální velikost uploadu, šifrování staženého balíčku a způsob bezpečné správy obnovovacího tajemství?

Rozhodnutí o e-mailovém poskytovateli, odesílací adrese, účetním formátu a případné bance nejsou blokátorem první etapy; budou vstupem do návrhu druhé etapy nebo dalšího rozšíření.

## Legislativní poznámka

Specifikace vychází z obecných náležitostí daňového dokladu podle § 29 zákona č. 235/2004 Sb., o DPH. Konkrétní šablony, výpočty a daňové režimy je nutné před produkčním použitím ověřit samostatně pro každý fakturační profil a potvrdit odpovědnou účetní nebo daňovým poradcem. Požadavky mají být konfigurovatelné tak, aby změna sazby nebo pravidla nevyžadovala přepis historických dokladů.

Referenční zdroje:

- [Zákon č. 235/2004 Sb., zejména § 29 – náležitosti daňového dokladu](https://www.zakonyprolidi.cz/cs/2004-235#p29)
- [Finanční správa – časté dotazy ke kontrolnímu hlášení a daňovým dokladům](https://financnisprava.gov.cz/cs/dane/dane/dan-z-pridane-hodnoty/kontrolni-hlaseni-dph/caste-dotazy-a-odpovedi)
- [ARES – F13 CyberTech s.r.o., IČO 06646654](https://ares.gov.cz/ekonomicke-subjekty?ico=06646654)
- [ARES – Martin Piech, IČO 02963281](https://ares.gov.cz/ekonomicke-subjekty?ico=02963281)
- [QR Platba – specifikace formátu SPAYD](https://qr-platba.cz/pro-vyvojare/specifikace-formatu/)
- [Cloudflare One – ověření Access JWT na aplikačním serveru](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)
