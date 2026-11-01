#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "Usage: $0 backups/miniclaw-YYYYMMDDTHHMMSSZ.dump" >&2
  exit 2
fi
backup_file="$(realpath "$1")"
recovery_db="miniclaw_restore_drill"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db dropdb --if-exists --username=miniclaw "$recovery_db"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db createdb --username=miniclaw --owner=miniclaw "$recovery_db"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db pg_restore --no-owner --username=miniclaw --dbname="$recovery_db" < "$backup_file"
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db psql --username=miniclaw --dbname="$recovery_db" -v ON_ERROR_STOP=1 -c 'SELECT current_database(), count(*) AS conversation_count FROM conversations;'
echo "Recovery drill restored into $recovery_db. Compare expected records, then remove the drill database when reviewed."
