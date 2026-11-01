#!/bin/sh
set -u
interval="${BACKUP_INTERVAL_HOURS:-24}"
retention="${BACKUP_RETENTION_DAYS:-14}"
while :; do
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  file="/backups/miniclaw-${stamp}.dump"
  if pg_dump --host=db --username=miniclaw --dbname=miniclaw --format=custom --no-owner --file="$file"; then
    printf '%s status=success file=%s\n' "$(date -u +%FT%TZ)" "$(basename "$file")" >> /backups/backup.log
  else
    rm -f "$file"
    printf '%s status=failed file=%s\n' "$(date -u +%FT%TZ)" "$(basename "$file")" >> /backups/backup.log
  fi
  keep_days="${retention:-14}"
  find /backups -maxdepth 1 -type f -name 'miniclaw-*.dump' -mtime "+$((keep_days - 1))" -delete
  sleep "$((interval * 3600))"
done
