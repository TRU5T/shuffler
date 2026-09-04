#!/usr/bin/env bash
# Run Shuffler against a real Unraid box over SSH (dev from this machine).
#
# Dry-run is on. Set the host, user and key/password in the UI gear dialog.
# Disks are listed under /mnt on the remote box, not the local fixture tree.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)

export SHUFFLER_STORAGE_BACKEND=ssh
export SHUFFLER_MOUNT_ROOT=/mnt
export SHUFFLER_DRY_RUN=true
export SHUFFLER_DB_PATH="$ROOT/data/shuffler.db"
export PYTHONPATH="$ROOT/backend"
unset SHUFFLER_SIMULATED_DISK_CAPACITY || true

# shellcheck disable=SC1091
. "$ROOT/scripts/free-dev-ports.sh"

echo "API      http://127.0.0.1:8756  (docs at /docs)"
echo "UI       http://127.0.0.1:5173"
echo "backend  SSH to Unraid  (host goes in the UI gear dialog)"
echo "disks    $SHUFFLER_MOUNT_ROOT on the remote box"
echo

trap 'kill 0' EXIT INT TERM

.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8756 --reload &

export NVM_DIR="$HOME/.nvm"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" && nvm use >/dev/null 2>&1 || true
(cd frontend && npm run dev) &

wait
