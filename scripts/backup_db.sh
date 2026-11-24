#!/usr/bin/env bash
# Back up the whole CahStopCap database (hats, orders, carousel, uploaded
# images) to a single compressed file.
#
# Usage:
#   DATABASE_URL='postgresql://user:pass@host:5432/dbname' scripts/backup_db.sh [backup-dir]
#
#   - On Render: copy the "External Database URL" from the database's page.
#   - Needs `pg_dump` (PostgreSQL client tools) with a version at least as
#     new as the database server.
#   - Keeps the newest 30 backups in the folder and deletes older ones.
#
# Restore a backup into an (empty) database with:
#   pg_restore --no-owner --clean --if-exists -d "$DATABASE_URL" backups/cahstopcap-YYYY-MM-DD_HHMM.dump
#
# Run it on a schedule (example: every night at 3am, with cron on a computer
# that's always on, e.g. the mini PC):
#   0 3 * * * cd /path/to/cahstopcap && DATABASE_URL='...' scripts/backup_db.sh >> backups/backup.log 2>&1
#
# Backups contain customer names, phone numbers and addresses: keep them
# private (never commit them; the backups/ folder is in .gitignore).

set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set" >&2
  exit 1
fi

# SQLAlchemy-style URLs (postgresql+psycopg2://...) aren't understood by pg_dump
url="${DATABASE_URL/postgresql+psycopg2:/postgresql:}"
url="${url/postgresql+psycopg:/postgresql:}"
url="${url/postgres:\/\//postgresql://}"

dir="${1:-backups}"
keep=30
mkdir -p "$dir"
chmod 700 "$dir"

file="$dir/cahstopcap-$(date +%Y-%m-%d_%H%M).dump"
umask 077
pg_dump --format=custom --no-owner --no-privileges --dbname="$url" --file="$file.partial"
mv "$file.partial" "$file"
echo "Backup written: $file ($(du -h "$file" | cut -f1))"

# Delete all but the newest $keep backups
ls -1t "$dir"/cahstopcap-*.dump 2>/dev/null | tail -n +$((keep + 1)) | xargs -r rm --
