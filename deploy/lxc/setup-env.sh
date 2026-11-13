#!/bin/bash
# Jednorázová (idempotentní) příprava jednoho prostředí aplikace faktury v LXC 102.
#
# Použití (jako root v LXC):
#   bash /srv/repos/tarolo-faktury/deploy/lxc/setup-env.sh staging 8102 faktury-test.vagasutca.org
#   bash /srv/repos/tarolo-faktury/deploy/lxc/setup-env.sh production 8101 faktury.vagasutca.org
# Skript je bezpečné spouštět opakovaně (po změnách v deploy/lxc/); doplní jen chybějící věci.
#
# Vytvoří: systémového uživatele faktury-<env>, adresáře /srv/apps/faktury-<env>,
# PostgreSQL roli a databázi faktury_<env>, /etc/faktury/<env>.env s náhodnými tajemstvími,
# systemd službu faktury-<env>.service, rotaci logu, deploy skript /usr/local/sbin/faktury-deploy
# a sudoers pravidlo, které runneru (uživatel ghrunner) dovolí spustit pouze tento deploy skript.
#
# Skript tajné údaje nikdy nevypisuje a existující env soubor nepřepisuje.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Spusť jako root." >&2
    exit 1
fi
if [ "$#" -ne 3 ]; then
    echo "Použití: $0 <env> <port> <hostname>" >&2
    exit 2
fi

ENV_NAME=$1
PORT=$2
HOSTNAME_PUBLIC=$3

[[ "$ENV_NAME" =~ ^(staging|production)$ ]] || { echo "env musí být staging nebo production" >&2; exit 2; }
[[ "$PORT" =~ ^81[0-9]{2}$ ]] || { echo "port musí být 8100-8199" >&2; exit 2; }
[[ "$HOSTNAME_PUBLIC" =~ ^[a-z0-9.-]+$ ]] || { echo "neplatný hostname" >&2; exit 2; }

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
APP_USER="faktury-$ENV_NAME"
APP_ROOT="/srv/apps/faktury-$ENV_NAME"
DB_NAME="faktury_$ENV_NAME"
ENV_DIR=/etc/faktury
ENV_FILE="$ENV_DIR/$ENV_NAME.env"
SERVICE="faktury-$ENV_NAME"
LOG_DIR="/var/log/faktury-$ENV_NAME"
RUNNER_USER=ghrunner
CF_TEAM_DOMAIN=${CF_ACCESS_TEAM_DOMAIN:-frosty-sound-32ef.cloudflareaccess.com}
CF_AUD=${CF_ACCESS_AUD:-e299b4e25a15ca4349afb4f8bdbbd04172b51de545fe85383db90c81404c9c2d}

echo "==> Balíčky"
apt-get update -qq
# libpango/libharfbuzz/fonty: generování PDF (WeasyPrint)
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sudo curl git python3-venv \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fontconfig fonts-dejavu-core >/dev/null

echo "==> Uživatelé"
if ! getent passwd "$APP_USER" >/dev/null; then
    useradd --system --home-dir "$APP_ROOT" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi
if ! getent passwd "$RUNNER_USER" >/dev/null; then
    useradd --system --create-home --home-dir "/home/$RUNNER_USER" --shell /bin/bash "$RUNNER_USER"
fi

echo "==> Adresáře"
install -d -o root -g "$APP_USER" -m 0750 "$APP_ROOT" "$APP_ROOT/releases"
install -d -o root -g root -m 0700 "$APP_ROOT/backups" "$APP_ROOT/backups/deploy" "$APP_ROOT/backups/nightly"
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$APP_ROOT/shared" "$APP_ROOT/shared/media" "$APP_ROOT/shared/cache"
install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$LOG_DIR"
[ -e "$LOG_DIR/application.log" ] || install -o "$APP_USER" -g "$APP_USER" -m 0640 /dev/null "$LOG_DIR/application.log"
install -d -o root -g root -m 0755 "$ENV_DIR"

echo "==> Proměnné prostředí ($ENV_FILE)"
if [ ! -f "$ENV_FILE" ]; then
    umask 077
    {
        printf 'DJANGO_SECRET_KEY=%s\n' "$(openssl rand -hex 32)"
        printf 'DJANGO_ALLOWED_HOSTS=%s,localhost,127.0.0.1\n' "$HOSTNAME_PUBLIC"
        printf 'DJANGO_CSRF_TRUSTED_ORIGINS=https://%s\n' "$HOSTNAME_PUBLIC"
        printf 'APP_ENVIRONMENT=%s\n' "$ENV_NAME"
        printf 'MEDIA_ROOT=%s\n' "$APP_ROOT/shared/media"
        printf 'POSTGRES_DB=%s\n' "$DB_NAME"
        printf 'POSTGRES_USER=%s\n' "$DB_NAME"
        printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 24)"
        printf 'POSTGRES_HOST=127.0.0.1\n'
        printf 'POSTGRES_PORT=5432\n'
        printf 'GUNICORN_BIND=127.0.0.1:%s\n' "$PORT"
    } > "$ENV_FILE"
    umask 022
    echo "    vytvořen"
else
    echo "    existuje, doplňuji jen chybějící klíče"
fi

ensure_env() {
    if ! grep -q "^$1=" "$ENV_FILE"; then
        printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"
        echo "    doplněno: $1"
    fi
}
ensure_env CF_ACCESS_TEAM_DOMAIN "$CF_TEAM_DOMAIN"
ensure_env CF_ACCESS_AUD "$CF_AUD"
if ! grep -q '^BOOTSTRAP_ADMIN_EMAILS=' "$ENV_FILE"; then
    ADMIN_EMAILS=${BOOTSTRAP_ADMIN_EMAILS:-}
    while ! [[ "$ADMIN_EMAILS" =~ ^[^@[:space:],]+@[^@[:space:],]+(,[^@[:space:],]+@[^@[:space:],]+)*$ ]]; do
        read -r -p "E-mail správce aplikace (stejný jako pro přihlášení do Cloudflare Access; více oddělit čárkou): " ADMIN_EMAILS
    done
    ensure_env BOOTSTRAP_ADMIN_EMAILS "$ADMIN_EMAILS"
fi
chown root:"$APP_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

echo "==> PostgreSQL role a databáze $DB_NAME"
DB_PASSWORD=$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_FILE")
if runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_roles WHERE rolname = '$DB_NAME'" | grep -q 1; then
    runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 -c "ALTER ROLE $DB_NAME WITH LOGIN PASSWORD '$DB_PASSWORD'" >/dev/null
else
    runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 -c "CREATE ROLE $DB_NAME WITH LOGIN PASSWORD '$DB_PASSWORD'" >/dev/null
fi
unset DB_PASSWORD
if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1; then
    runuser -u postgres -- createdb --owner="$DB_NAME" --encoding=UTF8 --template=template0 "$DB_NAME"
fi

echo "==> systemd služba $SERVICE"
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Faktury ($ENV_NAME)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=notify
NotifyAccess=main
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_ROOT/current/src
EnvironmentFile=$ENV_FILE
Environment=XDG_CACHE_HOME=$APP_ROOT/shared/cache
ExecStart=$APP_ROOT/current/venv/bin/gunicorn --bind \${GUNICORN_BIND} --workers 2 --threads 2 --timeout 60 --access-logfile - --error-logfile - config.wsgi:application
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=on-failure
RestartSec=5
UMask=0027
StandardOutput=append:$LOG_DIR/application.log
StandardError=append:$LOG_DIR/application.log

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_ROOT/shared
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE.service" >/dev/null

echo "==> Rotace logu"
cat > "/etc/logrotate.d/$SERVICE" <<EOF
$LOG_DIR/application.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    su $APP_USER $APP_USER
}
EOF

echo "==> Noční záloha (faktury-backup@$ENV_NAME.timer)"
install -o root -g root -m 0755 "$SCRIPT_DIR/faktury-backup" /usr/local/sbin/faktury-backup
cat > /etc/systemd/system/faktury-backup@.service <<'UNIT'
[Unit]
Description=Záloha databáze a souborů aplikace faktury (%i)
After=postgresql.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/faktury-backup %i
UNIT
cat > /etc/systemd/system/faktury-backup@.timer <<'UNIT'
[Unit]
Description=Noční záloha aplikace faktury (%i)

[Timer]
OnCalendar=*-*-* 02:30:00
RandomizedDelaySec=15min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now "faktury-backup@$ENV_NAME.timer" >/dev/null

echo "==> Deploy skript a sudoers"
install -o root -g root -m 0755 "$SCRIPT_DIR/faktury-deploy" /usr/local/sbin/faktury-deploy
SUDOERS_TMP=$(mktemp)
cat > "$SUDOERS_TMP" <<EOF
# Runner GitHub Actions smí spustit jen deploy skript (ten si argumenty sám ověřuje).
# Produkce: deploy skript sám ověří, že commit nese tag v*.
$RUNNER_USER ALL=(root) NOPASSWD: /usr/local/sbin/faktury-deploy staging *, /usr/local/sbin/faktury-deploy production *
EOF
visudo -cf "$SUDOERS_TMP" >/dev/null
install -o root -g root -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/faktury-deploy
rm -f "$SUDOERS_TMP"

echo
echo "Hotovo: prostředí '$ENV_NAME' připraveno (služba $SERVICE, port $PORT, https://$HOSTNAME_PUBLIC)."
echo "Služba se spustí při prvním nasazení (faktury-deploy $ENV_NAME <commit>)."
