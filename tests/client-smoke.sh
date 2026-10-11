#!/usr/bin/env bash
set -euo pipefail
PROXY_HOST="${PROXY_HOST:-127.0.0.1}"
HEALTH_URL="${HEALTH_URL:-https://www.gstatic.com/generate_204}"
status="$(curl -fsS --max-time 10 "http://${PROXY_HOST}:18080/json")"
jq -e '.xray_running == true and (.relay_pool | length <= 3) and (.exit_pool | length > 0)' <<<"$status" >/dev/null
jq -e '.route_mode | IN("direct_amsterdam", "chained_amsterdam", "subscription_exit")' <<<"$status" >/dev/null
jq -e '.dns_transport.direct_fallback == false' <<<"$status" >/dev/null
for proxy in "socks5h://${PROXY_HOST}:1080" "http://${PROXY_HOST}:8123"; do
  code="$(curl --noproxy "" -fsS --max-time 20 -x "$proxy" -o /dev/null -w '%{http_code}' "$HEALTH_URL")"
  test "$code" = 204
done
curl -fsS "http://${PROXY_HOST}:18080/" | grep -q 'Exit and delivery path'
printf 'HTTP, SOCKS, route status and DNS policy: OK\n'
