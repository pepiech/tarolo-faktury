# Tároló miniPC webserver – LXC 102

Tento adresář je provozní dokumentace a zdrojový kód první testovací aplikace na miniPC. Je napsaný tak, aby na něj mohl navázat i nový chat bez znalosti předchozí konverzace.

## Rychlý kontext pro nový chat

- Proxmox hostitel: `192.168.0.200`, Proxmox VE 9.0.11
- LXC: VMID `102`, hostname `web-apps`, Debian 13, IP `192.168.0.203`
- LXC má 2 CPU, 2 GB RAM, 512 MB swap a 32GB disk
- Přístup z Windows: `ssh web-apps`
- SSH klíč na Windows: `C:\Users\Petr\.ssh\codex_web_apps_ed25519`
- SSH root přihlášení je povoleno pouze klíčem; heslo a keyboard-interactive jsou vypnuté
- UFW: výchozí `deny incoming`; port 22 je povolen jen z `192.168.0.0/24`
- Žádný webový port není otevřený v UFW
- Produkční záměr: aplikace publikovat ven přes Cloudflare Tunnel a případně chránit Cloudflare Access
- Doména spravovaná u Cloudflare: `vagasutca.org`

Stav ověřen 10. 11. 2026: `helloapp` i PostgreSQL jsou aktivní, health check je v pořádku, databáze používá UTF-8 a webový port není otevřený v UFW.

## Git repozitář

- Soukromý repozitář: `https://github.com/pepiech/tarolo-faktury`
- Hlavní větev: `main`
- Lokální pracovní kopie: `D:\projekty\tarolo-miniPC-webserver-102`
- Read-only deployment checkout v LXC: `/srv/repos/tarolo-faktury`
- Windows používá repo-specifický deploy klíč s právem zápisu, otisk `SHA256:dk7IjmWa84DheaM8nnGSm611p6euzzsfNKD7L/FUrD0`
- LXC používá samostatný deploy klíč pouze pro čtení, otisk `SHA256:NJ6ShNVWcpo9+CXcdUHjfSbjjMo5p+zT7l7gsNToWEU`

Windows používá SSH alias `github-tarolo-faktury`, takže `origin` má adresu `git@github-tarolo-faktury:pepiech/tarolo-faktury.git`. Běžná práce:

```powershell
Set-Location D:\projekty\tarolo-miniPC-webserver-102
git status
git add --all
git commit -m "Popis změny"
git push
```

Na LXC se změny pouze stahují:

```bash
git -C /srv/repos/tarolo-faktury pull --ff-only
```

Živý proces zatím běží z `/srv/apps/hello/src`; checkout v `/srv/repos` jej automaticky nepřepisuje. Budoucí deployment bude explicitní: záloha, fast-forward pull, instalace závislostí, migrace, static files, kontrola a restart služby. Soukromé klíče ani `/etc/helloapp.env` nejsou v repozitáři.

## Práce s LXC z Proxmox hostitele

Po přihlášení jako `root` na Proxmox (`192.168.0.200`) jsou užitečné tyto příkazy:

```bash
pct status 102
pct config 102
pct enter 102
pct exec 102 -- systemctl status helloapp --no-pager
pct reboot 102
pct shutdown 102
pct start 102
```

`pct enter 102` otevře root shell přímo v kontejneru a je záchranná cesta, pokud nefunguje SSH. Před restartem nebo vypnutím nejdřív ověřit, že neprobíhá migrace či záloha. Konfiguraci CPU, RAM, disků, sítě a snapshotů měnit na úrovni Proxmoxu, nikoli zevnitř LXC.

Z Windows je běžný přístup jednodušší:

```powershell
ssh web-apps
ssh web-apps "systemctl status helloapp --no-pager"
```

Alias `web-apps` je uložen v `C:\Users\Petr\.ssh\config`. Klíč nedávat na jiné počítače bez bezpečného přenosu a nikdy neposílat jeho soukromou část do chatu.

## Nasazená testovací aplikace

| Část | Hodnota |
|---|---|
| Název služby | `helloapp.service` |
| Zdroj v LXC | `/srv/apps/hello/src` |
| Virtuální prostředí | `/srv/apps/hello/venv` |
| Proměnné a tajné údaje | `/etc/helloapp.env` (root, režim 600) |
| Webový proces | Gunicorn, `127.0.0.1:8101` |
| Framework | Django 5.2 LTS |
| Databáze | lokální PostgreSQL, DB a role `helloapp` |
| Health check | `http://127.0.0.1:8101/health/` |

Port `8101` je úmyslně navázaný jen na loopback. Z Windows ani LAN se na něj nelze připojit přímo. Pro dočasné lokální zobrazení použij SSH tunel:

```powershell
ssh -N -L 18101:127.0.0.1:8101 web-apps
```

Pak otevři `http://127.0.0.1:18101`. Tunel ukončíš `Ctrl+C`.

## Běžná správa

```powershell
ssh web-apps
```

Uvnitř LXC:

```bash
systemctl status helloapp --no-pager
tail -n 100 /var/log/helloapp/application.log
systemctl restart helloapp
curl -fsS http://127.0.0.1:8101/health/
sudo -u postgres psql -l
ufw status verbose
```

Po změně zdrojů:

```bash
cd /srv/apps/hello/src
set -a
. /etc/helloapp.env
set +a
/srv/apps/hello/venv/bin/python manage.py check --deploy
/srv/apps/hello/venv/bin/python manage.py migrate
/srv/apps/hello/venv/bin/python manage.py collectstatic --noinput
systemctl restart helloapp
```

Zdrojový kód na Windows je autoritativně uložen v `D:\projekty\tarolo-miniPC-webserver-102\src`. Při budoucím nasazování nejprve udělat zálohu, zkopírovat změněné soubory přes `scp`, nastavit jejich vlastníka na `helloapp:helloapp`, spustit kontrolu a migrace a teprve potom restartovat službu. Skript `deploy/install.sh` slouží hlavně k reprodukci instalace na čistém serveru; tajné údaje negeneruje znovu, pokud `/etc/helloapp.env` už existuje.

## Bezpečnostní zásady

1. Neotevírat port 8101 v UFW ani na routeru.
2. PostgreSQL nepublikovat do LAN; používá se přes `127.0.0.1:5432`.
3. Obsah `/etc/helloapp.env` neposílat do chatu ani neukládat do Gitu.
4. Cloudflare chrání cestu k serveru, ale skutečná fakturační aplikace musí mít i vlastní uživatele, role, audit a bezpečné relace.
5. Pro každou další aplikaci použít samostatného systémového uživatele, databázovou roli, databázi, port a systemd službu.
6. Před aktualizací nebo migrací nejprve zálohovat databázi a uživatelská data.

Poznámka: `systemd-journald` v tomto neprivilegovaném LXC aktuálně selhává s `status=243/CREDENTIALS`, proto služba zapisuje stdout a stderr přímo do `/var/log/helloapp/application.log`. Log není veřejný. Před dlouhodobým provozem přidat rotaci logu nebo opravit konfiguraci LXC/journald z Proxmox hostitele.

## Cloudflare – zbývající krok

`cloudflared 2026.10.7` je v LXC nainstalovaný z oficiálního Cloudflare APT repozitáře. Vzdáleně spravovaný tunel `miniPC-webserver-102` běží jako aktivní a automaticky startovaná služba. Published application route `faktury.vagasutca.org` směruje na `http://localhost:8101` a v tunelu je zapnuté ověřování Access JWT.

Má se vytvořit samostatný vzdáleně spravovaný Cloudflare Tunnel `miniPC-webserver-102`. Veřejný hostname `faktury.vagasutca.org` bude směrovat na `http://localhost:8101`. Tunel je odchozí spojení, takže nevyžaduje veřejnou IP, port forwarding ani ruční aktualizaci měnící se IP.

Access aplikace `Faktury` chrání přesný hostname. Anonymní test 10. 11. 2026 vrátil `302` na přihlašovací stránku `frosty-sound-32ef.cloudflareaccess.com`; přihlášený uživatel byl přesměrován do Django aplikace. Přihlášení podporuje Cloudflare účet a One-time PIN; Allow politika obsahuje pouze schválené e-mailové adresy. Tunelový token je uložen pouze v `/etc/cloudflared/token` a nesmí se vkládat do tohoto souboru, Gitu ani do chatu.

Autorizační token nebo přihlašovací URL se nemají vkládat do dokumentace ani do chatu. Přihlášení provede majitel účtu Cloudflare. Pro administrativní nebo testovací aplikaci je vhodné před hostname zapnout Cloudflare Access.

Při volbě hostname je nutné přidat jej také do řádku `DJANGO_ALLOWED_HOSTS` v `/etc/helloapp.env` a restartovat `helloapp`. Pro reálnou aplikaci doplnit hostname rovněž do `CSRF_TRUSTED_ORIGINS` v nastavení Django.

## Zálohování a migrace

Minimální záloha aplikace obsahuje:

- PostgreSQL dump (`pg_dump` v custom formátu),
- zdrojový kód a případná uživatelská média,
- seznam Python závislostí,
- systemd unit a bezpečně uložené tajné údaje.

Zdrojový kód je také v tomto adresáři na Windows. Databázová hesla zde nejsou. Přesun na VPS nebo jiný server je přímočarý: nainstalovat PostgreSQL a Python, obnovit dump, nastavit proměnné, spustit migrace a přepnout Cloudflare Tunnel/DNS.

Plánované zálohy mohou mířit lokálně na notebook a šifrovaně na USB SSD připojené k Proxmox hostiteli v `/mnt/usb`. USB disk už obsahuje PhotoPrism data, takže není nezávislou zálohou těchto fotografií.

## Obsah adresáře

- `src/` – zdrojový kód Django aplikace
- `deploy/helloapp.service` – kopie systemd jednotky
- `deploy/helloapp.logrotate` – denní rotace aplikačního logu, 14 archivů
- `requirements.txt` – deklarované Python závislosti
- `requirements.lock.txt` – přesné instalované verze (doplněno po nasazení)
- `README.md` – tento provozní runbook

## Kontrolní seznam pro další chat

1. Nejdřív přečíst tento README.
2. Ověřit `ssh web-apps` a `systemctl status helloapp`.
3. Před změnami zkontrolovat volné místo, paměť a zálohu databáze.
4. Tajné údaje nikdy nevypisovat.
5. Dokončit Cloudflare Tunnel a Access až po potvrzení požadovaného hostname a přístupové politiky.
