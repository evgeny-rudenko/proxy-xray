#!/bin/sh
set -eu

PROXY_HOST="${PROXY_HOST:-proxy-xray}"
SOCKS_PORT="${SOCKS_PORT:-1080}"
HTTP_PORT="${HTTP_PORT:-8123}"
VLESS_PORT="${VLESS_PORT:-10086}"
STATUS_PORT="${STATUS_PORT:-18080}"
VLESS_ID="${VLESS_ID:-11111111-1111-4111-8111-111111111111}"
HEALTH_URL="${HEALTH_URL:-https://www.gstatic.com/generate_204}"
SPEED_URL="${SPEED_URL:-https://speed.cloudflare.com/__down?bytes=2000000}"
MIN_SPEED_KBPS="${MIN_SPEED_KBPS:-1000}"
RU_HEALTH_URL="${RU_HEALTH_URL:-https://ya.ru}"

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

info() {
    echo
    echo "== $* =="
}

http_code() {
    curl -sS --max-time 15 -o /dev/null -w "%{http_code}" "$@"
}

speed_kbps() {
    bytes_per_second="$(curl -f -sS --max-time 30 -o /dev/null -w "%{speed_download}" "$@")"
    awk -v bps="${bytes_per_second}" 'BEGIN { printf "%.0f", bps * 8 / 1000 }'
}

assert_204() {
    label="$1"
    shift
    code="$(http_code "$@" "${HEALTH_URL}")"
    echo "${label}: HTTP ${code}"
    [ "${code}" = "204" ] || fail "${label} expected 204, got ${code}"
}

assert_speed() {
    label="$1"
    shift
    kbps="$(speed_kbps "$@" "${SPEED_URL}")"
    echo "${label}: ${kbps} kbps"
    awk -v actual="${kbps}" -v min="${MIN_SPEED_KBPS}" 'BEGIN { exit !(actual >= min) }' \
        || fail "${label} speed ${kbps} kbps is below ${MIN_SPEED_KBPS} kbps"
}

info "status endpoint"
status_json="$(curl -fsS --max-time 10 "http://${PROXY_HOST}:${STATUS_PORT}/json")"
echo "${status_json}" | jq -e '.xray_running == true' >/dev/null || fail "xray_running is not true"
echo "${status_json}" | jq -e '.candidates_count > 0' >/dev/null || fail "no candidates in status"
echo "${status_json}" | jq -e '.last_subscription_success_at != null' >/dev/null \
    || fail "last_subscription_success_at is missing"
echo "${status_json}" | jq -e '.subscription_fetch.mode == "auto"' >/dev/null \
    || fail "subscription fetch mode is not auto"
echo "${status_json}" | jq -e '.subscription_fetch.proxy == "socks5h://127.0.0.1:1080"' >/dev/null \
    || fail "subscription fetch proxy is not local SOCKS"
echo "${status_json}" | jq -e '.subscription_fetch.last_method | type == "string"' >/dev/null \
    || fail "subscription fetch last_method is missing"
echo "${status_json}" | jq -e '.tested_live_candidates | type == "array"' >/dev/null \
    || fail "tested_live_candidates is not an array"
echo "${status_json}" | jq -e '
  .tested_live_candidates
  | [.[].fallback_score]
  | . as $scores
  | if length < 2 then true else all(range(0; length - 1); $scores[.] >= $scores[. + 1]) end
' >/dev/null || fail "tested_live_candidates are not sorted by fallback score"
echo "${status_json}" | jq -e '.fallback.fallback_score | type == "number"' >/dev/null \
    || fail "fallback score is missing"
echo "${status_json}" | jq -e '.fallback.fallback_score_reasons | type == "array"' >/dev/null \
    || fail "fallback score reasons are missing"
echo "${status_json}" | jq -e '.candidate_checker.enabled == true' >/dev/null \
    || fail "candidate checker is not enabled"
echo "${status_json}" | jq -e '.candidate_checker.min_interval == 120 and .candidate_checker.max_interval == 300' >/dev/null \
    || fail "candidate checker interval is not 120..300"
echo "${status_json}" | jq -e '.candidate_checker.extra_weight == 5' >/dev/null \
    || fail "candidate checker extra weight is not 5"
echo "${status_json}" | jq -e '.standby_policy.max_age == 600 and .standby_policy.cooldown == 180 and .standby_policy.quarantine_duration == 900' >/dev/null \
    || fail "standby policy is not 600/180/900"
echo "${status_json}" | jq -e '.quarantine_count | type == "number"' >/dev/null \
    || fail "quarantine_count is missing"
echo "${status_json}" | jq -e '.failover_state.state | type == "string"' >/dev/null \
    || fail "failover_state is missing"
echo "${status_json}" | jq -e '.standby == null or (.standby.tag | type == "string")' >/dev/null \
    || fail "standby status is invalid"
echo "${status_json}" | jq -e '.active_backend.running == true and (.active_backend.candidate.tag | type == "string")' >/dev/null \
    || fail "active backend status is invalid"
echo "${status_json}" | jq -e '.hot_standby.running == true and (.hot_standby.candidate.tag | type == "string")' >/dev/null \
    || fail "hot standby status is invalid"
echo "${status_json}" | jq -e '.next_candidate_check_at != null' >/dev/null \
    || fail "next_candidate_check_at is missing"
echo "${status_json}" | jq -e '.routing.direct_domains | index("geosite:category-ru")' >/dev/null \
    || fail "geosite:category-ru is not configured as direct"
echo "${status_json}" | jq -e '.routing.direct_ips | index("geoip:ru")' >/dev/null \
    || fail "geoip:ru is not configured as direct"
echo "${status_json}" | jq -e '.active_path == null or (.active_path.balancer == "auto" and (.active_path.status | type == "string"))' >/dev/null \
    || fail "active_path status is invalid"
echo "${status_json}" | jq -e '.active_observatory == null or (.active_observatory.api_port | type == "number" and (.active_observatory.status | type == "string"))' >/dev/null \
    || fail "active_observatory status is invalid"
echo "${status_json}" | jq -e '.standby_observatory == null or (.standby_observatory.api_port | type == "number" and (.standby_observatory.status | type == "string"))' >/dev/null \
    || fail "standby_observatory status is invalid"
echo "${status_json}" | jq -e '.assets.items.geoip.status == "ok" and .assets.items.geosite.status == "ok"' >/dev/null \
    || fail "geo assets status is not ok"
echo "${status_json}" | jq -e '.assets.items.geoip.size > 1000000 and .assets.items.geosite.size > 1000000' >/dev/null \
    || fail "geo assets are too small"
echo "${status_json}" | jq -e '.assets.last_success_at != null or .assets.items.geoip.mtime != null' >/dev/null \
    || fail "geo assets timestamp is missing"
for health_key in xray_process socks_proxy http_proxy lan_vless quality_download throughput direct_internet subscription dns_ru dns_global telegram; do
    echo "${status_json}" | jq -e --arg key "${health_key}" '.health_checks[$key].status | type == "string"' >/dev/null \
        || fail "health check ${health_key} is missing"
done
echo "${status_json}" | jq -r '"xray_running=\(.xray_running) candidates=\(.candidates_count) extra=\(.sources.extra // 0) subscription=\(.sources.subscription // 0) fallback=\(.fallback.tag // "-")"'

info "published ports"
for port in "${SOCKS_PORT}" "${HTTP_PORT}" "${VLESS_PORT}" "${STATUS_PORT}" 53; do
    nc -z -w 5 "${PROXY_HOST}" "${port}" || fail "port ${port} is not reachable"
    echo "port ${port}: reachable"
done

info "SOCKS proxy"
assert_204 "socks health" -x "socks5h://${PROXY_HOST}:${SOCKS_PORT}"
assert_speed "socks throughput" -x "socks5h://${PROXY_HOST}:${SOCKS_PORT}"

info "HTTP proxy"
assert_204 "http health" -x "http://${PROXY_HOST}:${HTTP_PORT}"
assert_speed "http throughput" -x "http://${PROXY_HOST}:${HTTP_PORT}"

info "LAN VLESS inbound"
cat >/tmp/lan-vless-client.json <<EOF
{
  "log": {"loglevel": "warning"},
  "inbounds": [
    {
      "tag": "test-socks",
      "listen": "127.0.0.1",
      "port": 19081,
      "protocol": "socks",
      "settings": {"udp": false}
    }
  ],
  "outbounds": [
    {
      "tag": "home-vless",
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "${PROXY_HOST}",
            "port": ${VLESS_PORT},
            "users": [
              {
                "id": "${VLESS_ID}",
                "encryption": "none"
              }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "none"
      }
    }
  ]
}
EOF

/usr/local/bin/xray -c /tmp/lan-vless-client.json >/tmp/lan-vless-client.log 2>&1 &
vless_pid="$!"
trap 'kill "${vless_pid}" 2>/dev/null || true' EXIT
sleep 2
assert_204 "vless health" -x "socks5h://127.0.0.1:19081"
assert_speed "vless throughput" -x "socks5h://127.0.0.1:19081"

info "status logs"
curl -fsS --max-time 10 "http://${PROXY_HOST}:${STATUS_PORT}/logs" | tail -n 10

info "status timezone"
status_html="$(curl -fsS --max-time 10 "http://${PROXY_HOST}:${STATUS_PORT}/")"
echo "${status_html}" | grep -q "Europe/Moscow" || fail "status page does not show Europe/Moscow timezone"
echo "${status_html}" | grep -q "UTC+03:00" || fail "status page does not show UTC+03:00 offset"
echo "${status_html}" | grep -q "Health indicators" || fail "status page does not show health indicators"
echo "${status_html}" | grep -q "Current connection" || fail "status page does not show current connection"
echo "${status_html}" | grep -q "Hot standby" || fail "status page does not show hot standby"
echo "${status_html}" | grep -q "Switch guard" || fail "status page does not show switch guard"
echo "${status_html}" | grep -q "Routing and assets" || fail "status page does not show routing/assets"
echo "${status_html}" | grep -q "Direct internet" || fail "status page does not show direct internet health"
echo "${status_html}" | grep -q "LAN VLESS inbound" || fail "status page does not show LAN VLESS health"
echo "timezone: Europe/Moscow (UTC+03:00)"

info "split DNS"
class_ru="$(/dns-split-proxy.py --classify yandex.ru)"
class_su="$(/dns-split-proxy.py --classify nic.su)"
class_rf="$(/dns-split-proxy.py --classify президент.рф)"
class_global="$(/dns-split-proxy.py --classify google.com)"
echo "classify yandex.ru=${class_ru} nic.su=${class_su} президент.рф=${class_rf} google.com=${class_global}"
[ "${class_ru}" = "ru" ] || fail "yandex.ru must use ru DNS upstream"
[ "${class_su}" = "ru" ] || fail "nic.su must use ru DNS upstream"
[ "${class_rf}" = "ru" ] || fail "президент.рф must use ru DNS upstream"
[ "${class_global}" = "global" ] || fail "google.com must use global DNS upstream"
python3 - <<'PY'
import importlib.util

spec = importlib.util.spec_from_file_location("dns_split_proxy", "/dns-split-proxy.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

assert module.split_upstreams("8.8.8.8,1.1.1.1") == [("8.8.8.8", 53), ("1.1.1.1", 53)]

calls = []

def fake_query_tcp(packet, upstream, timeout):
    calls.append(upstream)
    if upstream[0] == "broken":
        raise OSError("simulated failure")
    return b"ok"

module.query_tcp = fake_query_tcp
module.DNSHandler.global_upstreams = [("broken", 53), ("backup", 53)]
module.DNSHandler.ru_upstreams = [("ru", 53)]
module.DNSHandler.last_good = {"ru": 0, "global": 0}
assert module.DNSHandler.resolve(b"\x00" * 12) == b"ok"
assert calls == [("broken", 53), ("backup", 53)]
assert module.DNSHandler.last_good["global"] == 1
print("dns fallback parser: ok")
PY
python3 - <<'PY'
import sys
import random
import time

sys.path.insert(0, "/")
from proxy_xray.candidate_checker import weighted_choice
from proxy_xray.vless import native_candidate_order, primary_candidate, quarantine_candidate, standby_candidate

now = time.time()
random.seed(1)
failed_extra = {
    "index": 0,
    "uri": "extra",
    "name": "extra",
    "host": "extra.example",
    "port": 443,
    "source": "extra",
    "source_score": 0,
    "region_score": 2,
    "network_score": 1,
    "last_ok_at": None,
    "last_fail_at": now - 60,
}
live_sub = {
    "index": 1,
    "uri": "sub",
    "name": "sub",
    "host": "sub.example",
    "port": 443,
    "source": "subscription",
    "source_score": 1,
    "region_score": 1,
    "network_score": 1,
    "last_latency": 0.4,
    "last_ok_at": now - 60,
    "last_fail_at": None,
}
nonpreferred_sub = {
    "index": 2,
    "uri": "nonpreferred",
    "name": "Nigeria",
    "host": "nigeria.example",
    "port": 443,
    "source": "subscription",
    "source_score": 1,
    "region_score": 2,
    "network_score": 0,
    "last_latency": 0.3,
    "last_ok_at": now - 10,
    "last_fail_at": None,
}
ordered = native_candidate_order([failed_extra, live_sub])
assert ordered[0]["uri"] == "sub", ordered
assert ordered[0]["fallback_score"] > ordered[1]["fallback_score"]
ordered = native_candidate_order([live_sub, nonpreferred_sub])
assert ordered[0]["uri"] == "sub", ordered
assert "non-preferred-region" in " ".join(nonpreferred_sub["fallback_score_reasons"])
assert primary_candidate([nonpreferred_sub, live_sub])["uri"] == "sub"
quarantine_candidate(live_sub, 900, "test quarantine", now=now)
ordered = native_candidate_order([failed_extra, live_sub])
assert ordered[0]["uri"] == "extra", ordered
live_sub["last_fail_at"] = None
live_sub["last_ok_at"] = now - 60
live_sub["quarantine_until"] = None
live_sub["quarantine_reason"] = None
live_sub["last_ok_at"] = now - 500
assert standby_candidate([failed_extra, live_sub, nonpreferred_sub], primary=failed_extra, max_age=600, now=now)["uri"] == "sub"
live_sub["last_ok_at"] = now - 60
assert standby_candidate([failed_extra, live_sub], primary=failed_extra, max_age=600, now=now)["uri"] == "sub"
weighted = [weighted_choice([failed_extra, live_sub], type("Args", (), {"candidate_check_extra_weight": 5})())["uri"] for _ in range(200)]
assert weighted.count("extra") > weighted.count("sub"), weighted
print("fallback scoring: ok")
PY

nslookup yandex.ru "${PROXY_HOST}" >/tmp/nslookup-ru.txt 2>&1 || fail "failed to resolve yandex.ru through split DNS"
nslookup google.com "${PROXY_HOST}" >/tmp/nslookup-global.txt 2>&1 || fail "failed to resolve google.com through split DNS"
grep -Eq "Address [0-9]+:|Address:" /tmp/nslookup-ru.txt || fail "yandex.ru DNS response has no address"
grep -Eq "Address [0-9]+:|Address:" /tmp/nslookup-global.txt || fail "google.com DNS response has no address"
grep -E "Address|Name" /tmp/nslookup-ru.txt | tail -n 5
grep -E "Address|Name" /tmp/nslookup-global.txt | tail -n 5

info "ru direct routing smoke"
ru_code="$(http_code -x "socks5h://${PROXY_HOST}:${SOCKS_PORT}" "${RU_HEALTH_URL}")"
echo "ru health through socks: HTTP ${ru_code}"
case "${ru_code}" in
    200|204|301|302) ;;
    *) fail "unexpected RU health HTTP code ${ru_code}" ;;
esac

info "geoip/geosite assets"
[ -s /opt/proxy-xray/assets/geoip.dat ] || fail "geoip.dat is missing in persistent assets"
[ -s /opt/proxy-xray/assets/geosite.dat ] || fail "geosite.dat is missing in persistent assets"
geoip_size="$(wc -c </opt/proxy-xray/assets/geoip.dat)"
geosite_size="$(wc -c </opt/proxy-xray/assets/geosite.dat)"
echo "persistent geoip.dat bytes=${geoip_size}"
echo "persistent geosite.dat bytes=${geosite_size}"
[ "${geoip_size}" -gt 1000000 ] || fail "geoip.dat is unexpectedly small"
[ "${geosite_size}" -gt 1000000 ] || fail "geosite.dat is unexpectedly small"
curl -fsSIL --max-time 20 https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat \
    | awk '/^[Hh][Tt][Tt][Pp]\// || /^[Ll]ast-[Mm]odified:/ || /^[Ee][Tt]ag:/ {print}'

info "all tests passed"
