#!/usr/bin/env bash

set -e

usage() {
    echo "proxy-xray subscription gateway"
    echo "    --sub-url <subscription-url>          Load VLESS servers from a subscription URL"
    echo "    --sub-extra-file <path>               Load prioritized local VLESS URIs"
    echo "    --sub-extra-vless <vless-uri>         Add one prioritized VLESS URI"
    echo "    --sub-prefer <regions>                Preferred subscription regions, default: us,eu"
    echo "    --sub-exclude <markers>               Excluded subscription markers"
    echo "    --sub-refresh-interval <seconds>      Subscription refresh interval"
    echo "    --sub-fetch-mode <direct|proxy|auto>  Subscription fetch route"
    echo "    --sub-fetch-proxy <proxy-url>         Proxy used for subscription fetch"
    echo "    --sub-post-start-refresh-delay <s>    Retry failed startup refresh after Xray starts"
    echo "    --sub-check-interval <seconds>        Active connection health-check interval"
    echo "    --sub-max-failures <count>            Failed health checks before failover"
    echo "    --sub-degrade-latency <seconds>       Slow active health-check threshold"
    echo "    --sub-degrade-checks <count>          Slow checks before failover"
    echo "    --active-pool-size <count>            VLESS candidates in the active Xray slot"
    echo "    --standby-pool-size <count>           VLESS candidates in the hot standby Xray slot"
    echo "    --sub-retry-interval <seconds>        Wait before retrying when no servers work"
    echo "    --sub-state-file <path>               Candidate state path"
    echo "    --sub-health-url <url>                URL used for health checks"
    echo "    --sub-observatory-probe-interval <d>  Xray observatory probe interval"
    echo "    --sub-balancer-strategy <strategy>    Xray balancer strategy"
    echo "    --dns-global <ip[,ip...]>             Global upstream DNS servers"
    echo "    --dns-ru <ip[,ip...]>                 RU upstream DNS servers"
    echo "    --dns-split-ru                        Resolve .ru, .su and .рф through RU DNS"
    echo "    --throughput-check-interval <seconds> Active path throughput interval"
    echo "    --throughput-url <url>                URL used for throughput checks"
    echo "    --throughput-min-kbps <kbps>          Minimum acceptable throughput"
    echo "    --throughput-max-time <seconds>       Throughput check timeout"
    echo "    --throughput-degrade-checks <count>   Slow throughput checks before failover"
    echo "    --quality-check-interval <seconds>    Small download quality-check interval"
    echo "    --quality-url <url>                   URL used for small quality checks"
    echo "    --quality-min-kbps <kbps>             Minimum acceptable quality-check speed"
    echo "    --quality-max-time <seconds>          Small quality-check timeout"
    echo "    --quality-degrade-checks <count>      Slow quality checks before failover"
    echo "    --standby-max-age <seconds>           Maximum age of a hot standby OK check"
    echo "    --failover-cooldown <seconds>         Suppress degraded failover after switch"
    echo "    --hot-standby-fast-failures <count>   Failures before using healthy hot standby"
    echo "    --quarantine-duration <seconds>       Soft-quarantine failed primary duration"
    echo "    --candidate-check-min-interval <s>    Minimum random candidate check delay"
    echo "    --candidate-check-max-interval <s>    Maximum random candidate check delay"
    echo "    --candidate-check-timeout <seconds>   Per-candidate health-check timeout"
    echo "    --candidate-check-extra-weight <n>    Extra-list candidate check weight"
    echo "    --active-path-interval <seconds>      Xray balancer status refresh interval"
    echo "    --asset-dir <path>                    Persistent geo asset directory"
    echo "    --asset-refresh-interval <seconds>    LoyalSoldier geo asset refresh interval"
    echo "    --asset-fetch-timeout <seconds>       LoyalSoldier geo asset download timeout"
    echo "    --no-asset-refresh-on-start           Do not refresh geo assets during startup"
    echo "    --status-listen <address>             Status web server listen address"
    echo "    --status-port <port>                  Status web server port"
    echo "    --inbound-vless                       Enable a plain VLESS inbound for LAN clients"
    echo "    --inbound-vless-port <port>           VLESS inbound port"
    echo "    --inbound-vless-id <uuid>             VLESS inbound client UUID"
    echo "    --inbound-vless-listen <address>      VLESS inbound listen address"
    echo "    --telegram-bot-token <token>          Telegram bot token"
    echo "    --telegram-chat-id <chat-id>          Telegram chat id"
    echo "    --domain-direct <domain-rule>         Route domain rule directly"
    echo "    --domain-proxy <domain-rule>          Route domain rule through proxy"
    echo "    --domain-block <domain-rule>          Block domain rule"
    echo "    --ip-direct <ip-rule>                 Route IP rule directly"
    echo "    --ip-proxy <ip-rule>                  Route IP rule through proxy"
    echo "    --ip-block <ip-rule>                  Block IP rule"
    echo "    -d|--debug                            Keep Xray logs on stdout/stderr"
}

need_value() {
    if [ $# -lt 2 ] || [ -z "$2" ]; then
        echo "missing value for $1" >&2
        usage >&2
        exit 1
    fi
}

first_csv_value() {
    local value="$1"
    value="${value%%,*}"
    echo "${value//[[:space:]]/}"
}

add_rule() {
    local outbound="$1"
    local field="$2"
    local value="$3"
    Jrules="$(jq -c --arg outbound "${outbound}" --arg field "${field}" --arg value "${value}" \
        '.rules += [{"type":"field", ($field): [$value], "outboundTag": $outbound}]' <<<"${Jrules}")"
}

start_dns() {
    if [ -n "${DNS_SPLIT_RU}" ]; then
        /dns-split-proxy.py --listen 0.0.0.0 --port 53 --dns-global "${DNS_GLOBAL}" --dns-ru "${DNS_RU}" &
        printf 'nameserver 127.0.0.1\noptions ndots:0\n' >/etc/resolv.conf
    else
        printf 'nameserver %s\noptions ndots:0\n' "$(first_csv_value "${DNS_GLOBAL}")" >/etc/resolv.conf
    fi
}

append_arg() {
    local value="$1"
    shift
    if [ -n "${value}" ]; then
        SUPERVISOR_ARGS+=("$@" "${value}")
    fi
}

SUB_URL=""
SUB_EXTRA_FILE=""
SUB_EXTRA_VLESS=()
SUB_PREFER=""
SUB_EXCLUDE=""
SUB_REFRESH_INTERVAL=""
SUB_FETCH_MODE=""
SUB_FETCH_PROXY=""
SUB_POST_START_REFRESH_DELAY=""
SUB_CHECK_INTERVAL=""
SUB_MAX_FAILURES=""
SUB_DEGRADE_LATENCY=""
SUB_DEGRADE_CHECKS=""
SUB_RETRY_INTERVAL=""
SUB_STATE_FILE=""
SUB_HEALTH_URL=""
SUB_OBSERVATORY_PROBE_INTERVAL=""
SUB_BALANCER_STRATEGY=""
ACTIVE_POOL_SIZE=""
STANDBY_POOL_SIZE=""

DNS_GLOBAL="8.8.8.8"
DNS_RU="77.88.8.8"
DNS_SPLIT_RU=""

THROUGHPUT_CHECK_INTERVAL=""
THROUGHPUT_URL=""
THROUGHPUT_MIN_KBPS=""
THROUGHPUT_MAX_TIME=""
THROUGHPUT_DEGRADE_CHECKS=""
QUALITY_CHECK_INTERVAL=""
QUALITY_URL=""
QUALITY_MIN_KBPS=""
QUALITY_MAX_TIME=""
QUALITY_DEGRADE_CHECKS=""
STANDBY_MAX_AGE=""
FAILOVER_COOLDOWN=""
HOT_STANDBY_FAST_FAILURES=""
QUARANTINE_DURATION=""
CANDIDATE_CHECK_MIN_INTERVAL=""
CANDIDATE_CHECK_MAX_INTERVAL=""
CANDIDATE_CHECK_TIMEOUT=""
CANDIDATE_CHECK_EXTRA_WEIGHT=""
ACTIVE_PATH_INTERVAL=""
ASSET_DIR=""
ASSET_REFRESH_INTERVAL=""
ASSET_FETCH_TIMEOUT=""
NO_ASSET_REFRESH_ON_START=""
STATUS_LISTEN=""
STATUS_PORT=""
INBOUND_VLESS=""
INBOUND_VLESS_PORT=""
INBOUND_VLESS_ID=""
INBOUND_VLESS_LISTEN=""
TELEGRAM_BOT_TOKEN_CLI=""
TELEGRAM_CHAT_ID_CLI=""
DEBUG=""
Jrules='{"rules":[]}'

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        -d|--debug)
            DEBUG=1
            shift
            ;;
        --sub-url)
            need_value "$@"; SUB_URL="$2"; shift 2
            ;;
        --sub-extra-file)
            need_value "$@"; SUB_EXTRA_FILE="$2"; shift 2
            ;;
        --sub-extra-vless)
            need_value "$@"; SUB_EXTRA_VLESS+=("$2"); shift 2
            ;;
        --sub-prefer)
            need_value "$@"; SUB_PREFER="$2"; shift 2
            ;;
        --sub-exclude)
            need_value "$@"; SUB_EXCLUDE="$2"; shift 2
            ;;
        --sub-refresh-interval)
            need_value "$@"; SUB_REFRESH_INTERVAL="$2"; shift 2
            ;;
        --sub-fetch-mode)
            need_value "$@"; SUB_FETCH_MODE="$2"; shift 2
            ;;
        --sub-fetch-proxy)
            need_value "$@"; SUB_FETCH_PROXY="$2"; shift 2
            ;;
        --sub-post-start-refresh-delay)
            need_value "$@"; SUB_POST_START_REFRESH_DELAY="$2"; shift 2
            ;;
        --sub-check-interval)
            need_value "$@"; SUB_CHECK_INTERVAL="$2"; shift 2
            ;;
        --sub-max-failures)
            need_value "$@"; SUB_MAX_FAILURES="$2"; shift 2
            ;;
        --sub-degrade-latency)
            need_value "$@"; SUB_DEGRADE_LATENCY="$2"; shift 2
            ;;
        --sub-degrade-checks)
            need_value "$@"; SUB_DEGRADE_CHECKS="$2"; shift 2
            ;;
        --sub-retry-interval)
            need_value "$@"; SUB_RETRY_INTERVAL="$2"; shift 2
            ;;
        --sub-state-file)
            need_value "$@"; SUB_STATE_FILE="$2"; shift 2
            ;;
        --sub-health-url)
            need_value "$@"; SUB_HEALTH_URL="$2"; shift 2
            ;;
        --sub-observatory-probe-interval)
            need_value "$@"; SUB_OBSERVATORY_PROBE_INTERVAL="$2"; shift 2
            ;;
        --sub-balancer-strategy)
            need_value "$@"; SUB_BALANCER_STRATEGY="$2"; shift 2
            ;;
        --active-pool-size)
            need_value "$@"; ACTIVE_POOL_SIZE="$2"; shift 2
            ;;
        --standby-pool-size)
            need_value "$@"; STANDBY_POOL_SIZE="$2"; shift 2
            ;;
        --dns|--dns-global)
            need_value "$@"; DNS_GLOBAL="$2"; shift 2
            ;;
        --dns-ru)
            need_value "$@"; DNS_RU="$2"; shift 2
            ;;
        --dns-split-ru)
            DNS_SPLIT_RU=1
            shift
            ;;
        --throughput-check-interval)
            need_value "$@"; THROUGHPUT_CHECK_INTERVAL="$2"; shift 2
            ;;
        --throughput-url)
            need_value "$@"; THROUGHPUT_URL="$2"; shift 2
            ;;
        --throughput-min-kbps)
            need_value "$@"; THROUGHPUT_MIN_KBPS="$2"; shift 2
            ;;
        --throughput-max-time)
            need_value "$@"; THROUGHPUT_MAX_TIME="$2"; shift 2
            ;;
        --throughput-degrade-checks)
            need_value "$@"; THROUGHPUT_DEGRADE_CHECKS="$2"; shift 2
            ;;
        --quality-check-interval)
            need_value "$@"; QUALITY_CHECK_INTERVAL="$2"; shift 2
            ;;
        --quality-url)
            need_value "$@"; QUALITY_URL="$2"; shift 2
            ;;
        --quality-min-kbps)
            need_value "$@"; QUALITY_MIN_KBPS="$2"; shift 2
            ;;
        --quality-max-time)
            need_value "$@"; QUALITY_MAX_TIME="$2"; shift 2
            ;;
        --quality-degrade-checks)
            need_value "$@"; QUALITY_DEGRADE_CHECKS="$2"; shift 2
            ;;
        --standby-max-age)
            need_value "$@"; STANDBY_MAX_AGE="$2"; shift 2
            ;;
        --failover-cooldown)
            need_value "$@"; FAILOVER_COOLDOWN="$2"; shift 2
            ;;
        --hot-standby-fast-failures)
            need_value "$@"; HOT_STANDBY_FAST_FAILURES="$2"; shift 2
            ;;
        --quarantine-duration)
            need_value "$@"; QUARANTINE_DURATION="$2"; shift 2
            ;;
        --candidate-check-min-interval)
            need_value "$@"; CANDIDATE_CHECK_MIN_INTERVAL="$2"; shift 2
            ;;
        --candidate-check-max-interval)
            need_value "$@"; CANDIDATE_CHECK_MAX_INTERVAL="$2"; shift 2
            ;;
        --candidate-check-timeout)
            need_value "$@"; CANDIDATE_CHECK_TIMEOUT="$2"; shift 2
            ;;
        --candidate-check-extra-weight)
            need_value "$@"; CANDIDATE_CHECK_EXTRA_WEIGHT="$2"; shift 2
            ;;
        --active-path-interval)
            need_value "$@"; ACTIVE_PATH_INTERVAL="$2"; shift 2
            ;;
        --asset-dir)
            need_value "$@"; ASSET_DIR="$2"; shift 2
            ;;
        --asset-refresh-interval)
            need_value "$@"; ASSET_REFRESH_INTERVAL="$2"; shift 2
            ;;
        --asset-fetch-timeout)
            need_value "$@"; ASSET_FETCH_TIMEOUT="$2"; shift 2
            ;;
        --no-asset-refresh-on-start)
            NO_ASSET_REFRESH_ON_START=1
            shift
            ;;
        --status-listen)
            need_value "$@"; STATUS_LISTEN="$2"; shift 2
            ;;
        --status-port)
            need_value "$@"; STATUS_PORT="$2"; shift 2
            ;;
        --inbound-vless)
            INBOUND_VLESS=1
            shift
            ;;
        --inbound-vless-port)
            need_value "$@"; INBOUND_VLESS_PORT="$2"; shift 2
            ;;
        --inbound-vless-id)
            need_value "$@"; INBOUND_VLESS_ID="$2"; shift 2
            ;;
        --inbound-vless-listen)
            need_value "$@"; INBOUND_VLESS_LISTEN="$2"; shift 2
            ;;
        --telegram-bot-token)
            need_value "$@"; TELEGRAM_BOT_TOKEN_CLI="$2"; shift 2
            ;;
        --telegram-chat-id)
            need_value "$@"; TELEGRAM_CHAT_ID_CLI="$2"; shift 2
            ;;
        --domain-direct)
            need_value "$@"; add_rule direct domain "$2"; shift 2
            ;;
        --domain-proxy)
            need_value "$@"; add_rule proxy domain "$2"; shift 2
            ;;
        --domain-block)
            need_value "$@"; add_rule block domain "$2"; shift 2
            ;;
        --ip-direct)
            need_value "$@"; add_rule direct ip "$2"; shift 2
            ;;
        --ip-proxy)
            need_value "$@"; add_rule proxy ip "$2"; shift 2
            ;;
        --ip-block)
            need_value "$@"; add_rule block ip "$2"; shift 2
            ;;
        --)
            shift
            break
            ;;
        *)
            echo "unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ $# -gt 0 ]; then
    echo "unexpected positional arguments: $*" >&2
    usage >&2
    exit 1
fi

if [ -z "${SUB_URL}" ]; then
    echo "--sub-url is required" >&2
    usage >&2
    exit 1
fi

RULES_FILE=/tmp/proxy-xray-rules.json
INJECT_FILE=/tmp/proxy-xray-inject.json
printf '%s\n' "${Jrules}" >"${RULES_FILE}"
printf '{}\n' >"${INJECT_FILE}"

start_dns

SUPERVISOR_ARGS=(--sub-url "${SUB_URL}" --dns "$(first_csv_value "${DNS_GLOBAL}")" --rules-file "${RULES_FILE}" --inject-file "${INJECT_FILE}")
append_arg "${SUB_EXTRA_FILE}" --extra-file
for extra_vless in "${SUB_EXTRA_VLESS[@]}"; do
    SUPERVISOR_ARGS+=(--extra-vless "${extra_vless}")
done
append_arg "${SUB_PREFER}" --prefer
append_arg "${SUB_EXCLUDE}" --exclude
append_arg "${SUB_REFRESH_INTERVAL}" --refresh-interval
append_arg "${SUB_FETCH_MODE}" --sub-fetch-mode
append_arg "${SUB_FETCH_PROXY}" --sub-fetch-proxy
append_arg "${SUB_POST_START_REFRESH_DELAY}" --sub-post-start-refresh-delay
append_arg "${SUB_CHECK_INTERVAL}" --check-interval
append_arg "${SUB_MAX_FAILURES}" --max-failures
append_arg "${SUB_DEGRADE_LATENCY}" --degrade-latency
append_arg "${SUB_DEGRADE_CHECKS}" --degrade-checks
append_arg "${SUB_RETRY_INTERVAL}" --retry-interval
append_arg "${SUB_STATE_FILE}" --state-file
append_arg "${SUB_HEALTH_URL}" --health-url
append_arg "${SUB_OBSERVATORY_PROBE_INTERVAL}" --observatory-probe-interval
append_arg "${SUB_BALANCER_STRATEGY}" --balancer-strategy
append_arg "${ACTIVE_POOL_SIZE}" --active-pool-size
append_arg "${STANDBY_POOL_SIZE}" --standby-pool-size
append_arg "${THROUGHPUT_CHECK_INTERVAL}" --throughput-check-interval
append_arg "${THROUGHPUT_URL}" --throughput-url
append_arg "${THROUGHPUT_MIN_KBPS}" --throughput-min-kbps
append_arg "${THROUGHPUT_MAX_TIME}" --throughput-max-time
append_arg "${THROUGHPUT_DEGRADE_CHECKS}" --throughput-degrade-checks
append_arg "${QUALITY_CHECK_INTERVAL}" --quality-check-interval
append_arg "${QUALITY_URL}" --quality-url
append_arg "${QUALITY_MIN_KBPS}" --quality-min-kbps
append_arg "${QUALITY_MAX_TIME}" --quality-max-time
append_arg "${QUALITY_DEGRADE_CHECKS}" --quality-degrade-checks
append_arg "${STANDBY_MAX_AGE}" --standby-max-age
append_arg "${FAILOVER_COOLDOWN}" --failover-cooldown
append_arg "${HOT_STANDBY_FAST_FAILURES}" --hot-standby-fast-failures
append_arg "${QUARANTINE_DURATION}" --quarantine-duration
append_arg "${CANDIDATE_CHECK_MIN_INTERVAL}" --candidate-check-min-interval
append_arg "${CANDIDATE_CHECK_MAX_INTERVAL}" --candidate-check-max-interval
append_arg "${CANDIDATE_CHECK_TIMEOUT}" --candidate-check-timeout
append_arg "${CANDIDATE_CHECK_EXTRA_WEIGHT}" --candidate-check-extra-weight
append_arg "${ACTIVE_PATH_INTERVAL}" --active-path-interval
append_arg "${ASSET_DIR}" --asset-dir
append_arg "${ASSET_REFRESH_INTERVAL}" --asset-refresh-interval
append_arg "${ASSET_FETCH_TIMEOUT}" --asset-fetch-timeout
append_arg "${STATUS_LISTEN}" --status-listen
append_arg "${STATUS_PORT}" --status-port
append_arg "${INBOUND_VLESS_PORT}" --inbound-vless-port
append_arg "${INBOUND_VLESS_ID}" --inbound-vless-id
append_arg "${INBOUND_VLESS_LISTEN}" --inbound-vless-listen
append_arg "${TELEGRAM_BOT_TOKEN_CLI}" --telegram-bot-token
append_arg "${TELEGRAM_CHAT_ID_CLI}" --telegram-chat-id

if [ -n "${NO_ASSET_REFRESH_ON_START}" ]; then
    SUPERVISOR_ARGS+=(--no-asset-refresh-on-start)
fi
if [ -n "${INBOUND_VLESS}" ]; then
    SUPERVISOR_ARGS+=(--inbound-vless)
fi
if [ -n "${DEBUG}" ]; then
    SUPERVISOR_ARGS+=(--debug)
fi

exec python3 /subscription-supervisor.py "${SUPERVISOR_ARGS[@]}"
