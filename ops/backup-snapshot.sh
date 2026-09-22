#!/usr/bin/env bash
# Spec section 9: a consistent snapshot of the live database.
#
# Copying the database file directly is unreliable in WAL mode -- recent
# changes live in a separate file, so a copy taken mid-write can restore
# stale or corrupt. It usually works, which is what makes it dangerous.
# VACUUM INTO writes a consistent, self-contained copy while the server is
# still writing.
#
# set -e matters here: without it a failing snapshot goes unnoticed and
# restic keeps backing up an old file forever.
set -euo pipefail

DB_PATH="${DB_PATH:-/srv/inventory/data/inventory.db}"
BACKUP_DIR="${BACKUP_DIR:-/srv/inventory/backup}"
SNAPSHOT="${BACKUP_DIR}/inventory-snapshot.db"

if [[ ! -f "${DB_PATH}" ]]; then
  echo "backup-snapshot: no database at ${DB_PATH}" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"

# VACUUM INTO refuses to overwrite an existing file, so write to a temporary
# name and move it into place. The move is atomic within one filesystem, so
# restic never sees a half-written snapshot.
TEMP_SNAPSHOT="$(mktemp -u "${BACKUP_DIR}/.inventory-snapshot.XXXXXX.db")"
trap 'rm -f "${TEMP_SNAPSHOT}"' EXIT

sqlite3 "${DB_PATH}" "VACUUM INTO '${TEMP_SNAPSHOT}'"
mv -f "${TEMP_SNAPSHOT}" "${SNAPSHOT}"

echo "backup-snapshot: wrote ${SNAPSHOT}"
