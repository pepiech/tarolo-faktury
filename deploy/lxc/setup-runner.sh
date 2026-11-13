#!/bin/bash
# Instalace self-hosted GitHub Actions runneru do LXC 102 (uživatel ghrunner, systemd služba).
# Runner se k GitHubu připojuje pouze odchozím HTTPS spojením; žádný port se neotevírá.
#
# Použití (jako root v LXC, po setup-env.sh):
#   bash /srv/repos/tarolo-faktury/deploy/lxc/setup-runner.sh
# Skript si vyžádá registrační token z
#   https://github.com/pepiech/tarolo-faktury/settings/actions/runners/new
# (token platí 1 hodinu a slouží jen k registraci).
set -euo pipefail

RUNNER_VERSION=${RUNNER_VERSION:-2.338.0}
# SHA-256 z poznámek k vydání https://github.com/actions/runner/releases/tag/v2.338.0
RUNNER_SHA256_X64=${RUNNER_SHA256:-af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32}
REPO_URL=https://github.com/pepiech/tarolo-faktury
RUNNER_USER=ghrunner
RUNNER_DIR=/opt/actions-runner
RUNNER_NAME=${RUNNER_NAME:-lxc102-web-apps}
RUNNER_LABELS=faktury-deploy

[ "$(id -u)" -eq 0 ] || { echo "Spusť jako root." >&2; exit 1; }
getent passwd "$RUNNER_USER" >/dev/null || { echo "Nejdřív spusť setup-env.sh (vytvoří uživatele $RUNNER_USER)." >&2; exit 1; }

case "$(uname -m)" in
    x86_64) ARCH=x64 ;;
    *) echo "Architektura $(uname -m) není ve skriptu podporovaná." >&2; exit 1 ;;
esac

if [ -f "$RUNNER_DIR/.runner" ]; then
    echo "Runner už je zaregistrovaný ($RUNNER_DIR/.runner). Stav služby:"
    (cd "$RUNNER_DIR" && ./svc.sh status) || true
    exit 0
fi

echo "==> Balíčky"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl tar ca-certificates libicu-dev >/dev/null

echo "==> Stahuji runner $RUNNER_VERSION"
TARBALL="actions-runner-linux-$ARCH-$RUNNER_VERSION.tar.gz"
TMP=$(mktemp -d)
curl -fsSL -o "$TMP/$TARBALL" "https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/$TARBALL"
echo "$RUNNER_SHA256_X64  $TMP/$TARBALL" | sha256sum -c --quiet - || {
    echo "Kontrolní součet nesouhlasí, instalace přerušena." >&2
    exit 1
}

install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0750 "$RUNNER_DIR"
runuser -u "$RUNNER_USER" -- tar -xzf "$TMP/$TARBALL" -C "$RUNNER_DIR"
rm -rf -- "$TMP"

echo "==> Závislosti runneru"
"$RUNNER_DIR/bin/installdependencies.sh" >/dev/null || echo "    (installdependencies.sh skončil chybou; libicu je nainstalované, pokračuji)"

echo
echo "Otevři $REPO_URL/settings/actions/runners/new"
echo "a zkopíruj hodnotu za '--token' z příkazu ./config.sh."
read -r -s -p "Registrační token: " TOKEN
echo
[ -n "$TOKEN" ] || { echo "Prázdný token." >&2; exit 1; }

echo "==> Registrace runneru $RUNNER_NAME (štítek $RUNNER_LABELS)"
runuser -u "$RUNNER_USER" -- "$RUNNER_DIR/config.sh" --unattended \
    --url "$REPO_URL" --token "$TOKEN" \
    --name "$RUNNER_NAME" --labels "$RUNNER_LABELS" \
    --work _work --replace
unset TOKEN

echo "==> systemd služba"
cd "$RUNNER_DIR"
./svc.sh install "$RUNNER_USER"
./svc.sh start
sleep 3
./svc.sh status | head -n 15 || true

echo
echo "Hotovo. Runner by měl být online na $REPO_URL/settings/actions/runners"
