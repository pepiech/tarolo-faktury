#!/bin/sh
set -eu

APP_USER=helloapp
APP_ROOT=/srv/apps/hello
APP_SRC=$APP_ROOT/src
APP_VENV=$APP_ROOT/venv
ENV_FILE=/etc/helloapp.env
SERVICE_FILE=/etc/systemd/system/helloapp.service

cluster_encoding="$(runuser -u postgres -- psql -Atqc "SHOW server_encoding")"
if [ "$cluster_encoding" != "UTF8" ]; then
    if runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname = 'helloapp'" | grep -q 1; then
        echo "Refusing to recreate a non-UTF8 cluster containing helloapp." >&2
        exit 1
    fi
    pg_dropcluster --stop 17 main
    pg_createcluster --start --locale=C.UTF-8 --encoding=UTF8 17 main
fi

if ! getent passwd "$APP_USER" >/dev/null; then
    useradd --system --home-dir "$APP_ROOT" --shell /usr/sbin/nologin "$APP_USER"
fi

install -d -o "$APP_USER" -g "$APP_USER" -m 0750 "$APP_ROOT" "$APP_SRC"

if [ ! -f "$ENV_FILE" ]; then
    umask 077
    db_password="$(openssl rand -hex 24)"
    django_secret="$(openssl rand -hex 32)"
    {
        printf 'DJANGO_SECRET_KEY=%s\n' "$django_secret"
        printf 'DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1\n'
        printf 'POSTGRES_DB=helloapp\n'
        printf 'POSTGRES_USER=helloapp\n'
        printf 'POSTGRES_PASSWORD=%s\n' "$db_password"
        printf 'POSTGRES_HOST=127.0.0.1\n'
        printf 'POSTGRES_PORT=5432\n'
    } > "$ENV_FILE"
fi
chown root:"$APP_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

set -a
. "$ENV_FILE"
set +a

if runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_roles WHERE rolname = 'helloapp'" | grep -q 1; then
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "ALTER ROLE helloapp WITH LOGIN PASSWORD '$POSTGRES_PASSWORD'" >/dev/null
else
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "CREATE ROLE helloapp WITH LOGIN PASSWORD '$POSTGRES_PASSWORD'" >/dev/null
fi

if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname = 'helloapp'" | grep -q 1; then
    runuser -u postgres -- createdb --owner=helloapp --encoding=UTF8 --template=template0 helloapp
fi

if [ ! -x "$APP_VENV/bin/python" ]; then
    python3 -m venv "$APP_VENV"
fi
"$APP_VENV/bin/python" -m pip install --upgrade pip
"$APP_VENV/bin/pip" install --requirement "$APP_ROOT/requirements.txt"

chown -R "$APP_USER:$APP_USER" "$APP_SRC" "$APP_VENV"
runuser -u "$APP_USER" -- "$APP_VENV/bin/python" "$APP_SRC/manage.py" migrate --noinput
runuser -u "$APP_USER" -- "$APP_VENV/bin/python" "$APP_SRC/manage.py" collectstatic --noinput
runuser -u "$APP_USER" -- "$APP_VENV/bin/python" "$APP_SRC/manage.py" check

install -d -o "$APP_USER" -g "$APP_USER" -m 0750 /var/log/helloapp
if [ ! -e /var/log/helloapp/application.log ]; then
    install -o "$APP_USER" -g "$APP_USER" -m 0640 /dev/null /var/log/helloapp/application.log
fi
install -o root -g root -m 0644 "$APP_ROOT/helloapp.service" "$SERVICE_FILE"
install -o root -g root -m 0644 "$APP_ROOT/helloapp.logrotate" /etc/logrotate.d/helloapp
systemctl daemon-reload
systemctl enable --now helloapp.service
systemctl restart helloapp.service

"$APP_VENV/bin/pip" freeze > "$APP_ROOT/requirements.lock.txt"
chown root:root "$APP_ROOT/requirements.lock.txt"
chmod 0644 "$APP_ROOT/requirements.lock.txt"

echo "Deployment complete."
