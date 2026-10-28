#!/usr/bin/env bash
# Deploy the tycoon to LXC 106 web (airport-tycoon.mattbrewer.me).
# Remote data/ (SQLite, secret_key) is never overwritten or deleted.
set -euo pipefail

HOST="${TYCOON_HOST:-mbrewski@10.145.22.63}"
REMOTE="${TYCOON_REMOTE:-develop/airport-tycoon}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
HEALTH_URL="http://127.0.0.1:8130/login"

echo "deploy airport-tycoon -> ${HOST}:${REMOTE}"
rsync -a --delete \
  --exclude .git/ \
  --exclude venv/ \
  --exclude data/ \
  --exclude __pycache__/ \
  --exclude '*.py[cod]' \
  "${ROOT}/" "${HOST}:${REMOTE}/"

ssh -o BatchMode=yes "$HOST" env REMOTE="$REMOTE" HEALTH_URL="$HEALTH_URL" bash -s <<'EOS'
set -euo pipefail
cd "$HOME/$REMOTE"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
mkdir -p data
if [[ ! -x venv/bin/python ]]; then
  python3 -m venv venv
fi
venv/bin/pip install -q -U pip
venv/bin/pip install -q -r requirements.txt
mkdir -p "$HOME/.config/systemd/user"
cp -f systemd/airport-tycoon.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now airport-tycoon.service
systemctl --user restart airport-tycoon.service
systemctl --user --quiet is-active airport-tycoon.service
for i in 1 2 3 4 5 6 7 8; do
  if curl -fsS --max-time 5 -o /dev/null "$HEALTH_URL"; then
    curl -fsS --max-time 5 -o /dev/null -w "health:%{http_code}\n" "$HEALTH_URL"
    exit 0
  fi
  sleep 1
done
echo "airport-tycoon did not answer ${HEALTH_URL}" >&2
systemctl --user --no-pager --full status airport-tycoon.service | tail -20 >&2
exit 1
EOS

echo "airport-tycoon deployed"
