#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
test -f .env || { echo 'Missing .env (copy .env.example and set production values).' >&2; exit 1; }
set -a
. ./.env
set +a
for name in POSTGRES_PASSWORD OPENAI_API_KEY FAMILY_ID MEMBER_ID APP_DOMAIN FAMILY_PHONE PUSH_VAPID_PUBLIC_KEY PUSH_VAPID_PRIVATE_KEY SMS_GATEWAY_URL SMS_GATEWAY_TOKEN; do
  eval "value=\${$name:-}"
  test -n "$value" || { echo "Missing required .env value: $name" >&2; exit 1; }
done
test "$POSTGRES_PASSWORD" != 'miniclaw-local' && test "$POSTGRES_PASSWORD" != 'change-me-for-local-development' || { echo 'Refusing a sample database password.' >&2; exit 1; }
test "${#POSTGRES_PASSWORD}" -ge 24 || { echo 'POSTGRES_PASSWORD must be at least 24 characters.' >&2; exit 1; }
test "$FAMILY_ID" != 'local-family' && test "$MEMBER_ID" != 'local-elder' || { echo 'Replace the development bootstrap family/member IDs.' >&2; exit 1; }
test "$APP_DOMAIN" != 'example.org' || { echo 'Replace the sample APP_DOMAIN with a real DNS name.' >&2; exit 1; }
test "$FAMILY_PHONE" != '+8613800000000' || { echo 'Replace the sample FAMILY_PHONE with the family contact number.' >&2; exit 1; }
case "$POSTGRES_PASSWORD" in *[!A-Za-z0-9_-]*) echo 'POSTGRES_PASSWORD may contain only letters, digits, _ and - for URL safety.' >&2; exit 1 ;; esac
docker compose -f docker-compose.yml -f docker-compose.prod.yml config >/dev/null
case "$FAMILY_PHONE" in *[!+0-9]*) echo 'FAMILY_PHONE may contain only digits and +.' >&2; exit 1 ;; esac
test "${#FAMILY_PHONE}" -ge 7 || { echo 'FAMILY_PHONE must include a dialable number.' >&2; exit 1; }
docker compose -f docker-compose.yml -f docker-compose.prod.yml build api web
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d db
echo 'Checking migrations before replacing running services...'
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm --no-deps --entrypoint alembic api upgrade head
echo 'Migration preflight passed; deploying production services...'
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --remove-orphans
attempt=0
until docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo 'API did not become healthy within five minutes.' >&2
    docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
    exit 1
  fi
  sleep 5
done
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
