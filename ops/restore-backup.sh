#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "Usage: $0 backups/miniclaw-YYYYMMDDTHHMMSSZ.dump" >&2
  exit 2
fi
backup_file="$(realpath "$1")"
echo "This replaces every record in the production database with: $backup_file"
echo 'Restore into a separate recovery database first unless production replacement is intended.'
printf 'Type RESTORE to continue: '
read -r answer
test "$answer" = RESTORE || { echo 'Restore cancelled.'; exit 1; }
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db pg_restore --clean --if-exists --no-owner --username=miniclaw --dbname=miniclaw < "$backup_file"
echo 'Restore command completed. Check API health and application data before reopening traffic.'
