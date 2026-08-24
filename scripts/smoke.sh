#!/bin/sh
set -eu
base="${1:-https://localhost}"
curl_opts="-fsS --max-time 10"
if [ "${SMOKE_INSECURE_TLS:-}" = "1" ]; then curl_opts="$curl_opts -k"; fi

echo "Checking liveness"
# shellcheck disable=SC2086
curl $curl_opts "$base/health/live" >/dev/null
echo "Checking readiness"
# shellcheck disable=SC2086
curl $curl_opts "$base/health/ready" >/dev/null
if [ "${WEB_ADMIN_ENABLED:-false}" = "true" ]; then
  echo "Checking optional web admin login page"
  # shellcheck disable=SC2086
  curl $curl_opts "$base/admin/login" >/dev/null
else
  echo "Web admin disabled; Telegram /admin is the management interface"
fi
echo "Smoke checks passed"
