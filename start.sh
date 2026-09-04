#!/usr/bin/env bash
# One-command local start.
#
#   ./start.sh           synthetic disks (safe, no Unraid needed)
#   ./start.sh unraid    real array over SSH (dry-run; set host in the UI)
set -euo pipefail

cd "$(dirname "$0")"
ROOT=$(pwd)
MODE="${1:-fixtures}"

if [ ! -x .venv/bin/python ]; then
  echo "creating .venv"
  python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements-dev.txt

export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm use
if [ ! -d frontend/node_modules ]; then
  echo "installing frontend deps"
  (cd frontend && npm install)
fi

mkdir -p "$ROOT/data"

case "$MODE" in
  fixtures)
    exec "$ROOT/scripts/dev-fixtures.sh"
    ;;
  unraid|ssh)
    exec "$ROOT/scripts/dev-ssh.sh"
    ;;
  *)
    echo "usage: $0 [fixtures|unraid]" >&2
    exit 1
    ;;
esac
