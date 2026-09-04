#!/usr/bin/env bash
# Run Shuffler against the synthetic fixture tree.
#
# Builds the fixtures if they are missing, then starts the API on :8756 and the
# Vite dev server on :5173. Nothing here can reach a real array.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)
FIXTURES=${FIXTURES:-/tmp/shuffler-fixtures}

if [ ! -d "$FIXTURES/mnt" ]; then
  echo "building fixtures in $FIXTURES"
  .venv/bin/python scripts/make_fixtures.py --root "$FIXTURES"
fi

export SHUFFLER_STORAGE_BACKEND=local
export SHUFFLER_MOUNT_ROOT="$FIXTURES/mnt"
export SHUFFLER_SIMULATED_DISK_CAPACITY=$((60 * 1024 * 1024 * 1024))
export SHUFFLER_DRY_RUN=true
export SHUFFLER_DB_PATH="$ROOT/data/fixtures.db"
export PYTHONPATH="$ROOT/backend"

# shellcheck disable=SC1091
. "$ROOT/scripts/free-dev-ports.sh"

echo "API      http://127.0.0.1:8756  (docs at /docs)"
echo "UI       http://127.0.0.1:5173"
echo "disks    $SHUFFLER_MOUNT_ROOT"
echo

trap 'kill 0' EXIT INT TERM

.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8756 --reload &

export NVM_DIR="$HOME/.nvm"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm use >/dev/null 2>&1 || true
(cd frontend && npm run dev) &

wait
