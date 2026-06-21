#!/bin/bash

DIR=`dirname $0`
DIR="$(cd $DIR; pwd)"
XCONF=/tmp/proxy-xray.json

usage() {
    echo "proxy-xray <connection-options>"
    echo "    --lgp  <VLESS-GRPC-PLN option>        id@host:port:svcname"
    echo "    --lgr  <VLESS-GRPC-RLTY option>       id@host:port:svcname,d=fakedest.com,pub=xxxx[,shortId=abcd]"
    echo "    --lgt  <VLESS-GRPC-TLS option>        id@host:port:svcname"
    echo "    --lsp  <VLESS-SPLT-PLN option>        id@host:port:/webpath"
    echo "    --lst  <VLESS-SPLT-TLS option>        id@host:port:/webpath[,alpn=h3]"
    echo "    --lst3 <VLESS-SPLT-TLS-HTTP3 option>  id@host:port:/webpath"
    echo "    --ltr  <VLESS-TCP-RLTY option>        id@host:port,d=dest.com,pub=xxxx[,shortId=abcd][,xtls]"
    echo "    --ltrx <VLESS-TCP-RLTY-XTLS option>   id@host:port,d=dest.com,pub=xxxx[,shortId=abcd]"
    echo "    --ltt  <VLESS-TCP-TLS option>         id@host:port[,xtls]"
    echo "    --lttx <VLESS-TCP-TLS-XTLS option>    id@host:port"
    echo "    --lwp  <VLESS-WS-PLN option>          id@host:port:/wspath"
    echo "    --lwt  <VLESS-WS-TLS option>          id@host:port:/wspath"
    echo "    --mtt  <VMESS-TCP-TLS option>         id@host:port"
    echo "    --mwp  <VMESS-WS-PLN option>          id@host:port:/wspath"
    echo "    --mwt  <VMESS-WS-TLS option>          id@host:port:/wspath"
    echo "    --ttt  <TROJAN-TCP-TLS option>        password@host:port"
    echo "    --twp  <TROJAN-WS-PLN option>         password@host:port:/wspath"
    echo "    --twt  <TROJAN-WS-TLS option>         password@host:port:/wspath"
    echo "    -d|--debug                            Start in debug mode with verbose output"
    echo "    -i|--stdin                            Read config from stdin instead of auto generation"
    echo "    -j|--json                             Json snippet to merge into the config. Say '{"log":{"loglevel":"info"}}'"
    echo "    --dns  <upstream-DNS-ip>              Alias for --dns-global, 8.8.8.8 will be applied by default"
    echo "    --dns-global <ip[,ip...]>             Global upstream DNS servers, default: 8.8.8.8"
    echo "    --dns-ru <ip[,ip...]>                 Russian upstream DNS servers, default: 77.88.8.8"
    echo "    --dns-split-ru                        Resolve .ru, .su and .рф via --dns-ru directly"
#   echo "    --dns-local <local-conf-file>         Enable designated domain conf file. Like apple.china.conf"
    echo "    --dns-local-cn                        Enable China-accessible domains to be resolved in China"
    echo "    --sub-url <subscription-url>          Load VLESS servers from a subscription URL and auto-failover"
    echo "    --sub-extra-file <path>               Load additional prioritized VLESS URIs from a local file"
    echo "    --sub-extra-vless <vless-uri>         Add one prioritized VLESS URI"
    echo "    --sub-prefer <regions>                Preferred subscription regions, default: us,eu"
    echo "    --sub-exclude <markers>               Excluded subscription markers, default: ru,russia,россия"
    echo "    --sub-refresh-interval <seconds>      Subscription refresh interval, default: 86400"
    echo "    --sub-fetch-mode <direct|proxy|auto>  Subscription fetch route, default: auto"
    echo "    --sub-fetch-proxy <proxy-url>         Proxy used for subscription fetch, default: socks5h://127.0.0.1:1080"
    echo "    --sub-post-start-refresh-delay <s>    Retry failed startup subscription refresh after Xray starts, default: 15"
    echo "    --sub-check-interval <seconds>        Active connection health-check interval, default: 60"
    echo "    --sub-max-failures <count>            Failed health checks before failover, default: 3"
    echo "    --sub-degrade-latency <seconds>       Slow active health-check threshold, default: 6"
    echo "    --sub-degrade-checks <count>          Slow checks before failover, default: 3"
    echo "    --sub-retry-interval <seconds>        Wait before retrying when no servers work, default: 300"
    echo "    --sub-state-file <path>               Persist candidate speed state, default: /var/lib/proxy-xray/state.json"
    echo "    --sub-health-url <url>                URL used for health and speed checks"
    echo "    --sub-observatory-probe-interval <d>  Xray observatory probe interval, default: 10s"
    echo "    --sub-balancer-strategy <strategy>    Xray balancer strategy, default: leastPing"
    echo "    --throughput-check-interval <seconds> Active path throughput check interval, default: 300"
    echo "    --throughput-url <url>                URL used for active path throughput checks"
    echo "    --throughput-min-kbps <kbps>          Minimum acceptable active path throughput, default: 1500"
    echo "    --throughput-max-time <seconds>       Throughput check timeout, default: 20"
    echo "    --throughput-degrade-checks <count>   Slow throughput checks before restart, default: 3"
    echo "    --standby-max-age <seconds>           Maximum age of a hot standby OK check, default: 600"
    echo "    --failover-cooldown <seconds>         Suppress degraded failover after switch, default: 180"
    echo "    --quarantine-duration <seconds>       Soft-quarantine failed primary after switch, default: 900"
    echo "    --candidate-check-min-interval <s>    Minimum random per-candidate check delay, default: 120"
    echo "    --candidate-check-max-interval <s>    Maximum random per-candidate check delay, default: 300"
    echo "    --candidate-check-timeout <seconds>   Per-candidate health-check timeout, default: 10"
    echo "    --candidate-check-extra-weight <n>    Extra-list candidate check weight, default: 5"
    echo "    --active-path-interval <seconds>      Xray balancer status refresh interval, default: 15"
    echo "    --asset-dir <path>                    Persistent geo asset directory, default: /opt/proxy-xray/assets"
    echo "    --asset-refresh-interval <seconds>    LoyalSoldier geo asset refresh interval, default: 86400"
    echo "    --asset-fetch-timeout <seconds>       LoyalSoldier geo asset download timeout, default: 30"
    echo "    --no-asset-refresh-on-start           Do not refresh geo assets during startup"
    echo "    --status-listen <address>             Status web server listen address, default: 0.0.0.0"
    echo "    --status-port <port>                  Status web server port, default: 18080"
    echo "    --inbound-vless                      Enable a plain VLESS inbound for LAN clients"
    echo "    --inbound-vless-port <port>           VLESS inbound port, default: 10086"
    echo "    --inbound-vless-id <uuid>             VLESS inbound client UUID"
    echo "    --inbound-vless-listen <address>      VLESS inbound listen address, default: 0.0.0.0"
    echo "    --telegram-bot-token <token>          Telegram bot token for successful failover notifications"
    echo "    --telegram-chat-id <chat-id>          Telegram chat id for notifications"
    echo "    --domain-direct <domain-rule>         Add a domain rule for direct routing, like geosite:geosite:geolocation-cn"
    echo "    --domain-proxy  <domain-rule>         Add a domain rule for proxy routing, like twitter.com or geosite:google-cn"
    echo "    --domain-block  <domain-rule>         Add a domain rule for block routing, like geosite:category-ads-all"
    echo "    --ip-direct     <ip-rule>             Add a ip-addr rule for direct routing, like 114.114.114.114/32 or geoip:cn"
    echo "    --ip-proxy      <ip-rule>             Add a ip-addr rule for proxy routing, like 1.1.1.1/32 or geoip:netflix"
    echo "    --ip-block      <ip-rule>             Add a ip-addr rule for block routing, like geoip:private"
    echo "    --cn-direct                           Add routing rules to avoid domains and IPs located in China being proxied"
    echo "    --rules-path    <rules-dir-path>      Folder path contents geoip.dat, geosite.dat and other rule files"
}


Jrules='{"rules":[]}'

TEMP=`getopt -o j:di --long lgp:,lgr:,lgt:,lsp:,lst:,lst3:,ltr:,ltrx:,ltt:,lttx:,lwp:,lwt:,mtt:,mwp:,mwt:,ttt:,twp:,twt:,stdin,debug,dns:,dns-global:,dns-ru:,dns-split-ru,dns-local:,dns-local-cn,sub-url:,sub-extra-file:,sub-extra-vless:,sub-prefer:,sub-exclude:,sub-refresh-interval:,sub-fetch-mode:,sub-fetch-proxy:,sub-post-start-refresh-delay:,sub-check-interval:,sub-max-failures:,sub-degrade-latency:,sub-degrade-checks:,sub-retry-interval:,sub-state-file:,sub-health-url:,sub-observatory-probe-interval:,sub-balancer-strategy:,throughput-check-interval:,throughput-url:,throughput-min-kbps:,throughput-max-time:,throughput-degrade-checks:,standby-max-age:,failover-cooldown:,quarantine-duration:,candidate-check-min-interval:,candidate-check-max-interval:,candidate-check-timeout:,candidate-check-extra-weight:,active-path-interval:,asset-dir:,asset-refresh-interval:,asset-fetch-timeout:,no-asset-refresh-on-start,status-listen:,status-port:,inbound-vless,inbound-vless-port:,inbound-vless-id:,inbound-vless-listen:,telegram-bot-token:,telegram-chat-id:,domain-direct:,domain-proxy:,domain-block:,ip-direct:,ip-proxy:,ip-block:,cn-direct,rules-path:,json: -n "$0" -- $@`
if [ $? != 0 ] ; then usage; exit 1 ; fi
eval set -- "$TEMP"
while true ; do
    case "$1" in
        --lgp|--lgr|--lgt|--lsp|--lst|--ltr|--ltt|--lwp|--lwt|--mtt|--mwp|--mwt|--ttt|--twp|--twt)
            subcmd=`echo "$1"|tr -d "\-\-"`
            PXCMD="$DIR/proxy-${subcmd}.sh $2"
            shift 2
            ;;
        --ltrx|--lttx)
            # Alias of --ltr|ltt options
            subcmd=`echo $1|tr -d '\-\-'|tr -d x`
            PXCMD="$DIR/proxy-${subcmd}.sh $2,xtls"
            shift 2
            ;;
        --lst3)
            # Alias of --lst options
            # splitHTTP is the only option for H3 support from Xray-Core so far.
            subcmd=`echo $1|tr -d '\-\-'|tr -d 3`
            PXCMD="$DIR/proxy-${subcmd}.sh $2,alpn=h3"
            shift 2
            ;;
        --dns)
            DNSGLOBAL=$2
            DNS=$2
            shift 2
            ;;
        --dns-global)
            DNSGLOBAL=$2
            DNS=$2
            shift 2
            ;;
        --dns-ru)
            DNSRU=$2
            shift 2
            ;;
        --dns-split-ru)
            DNSSPLITRU=1
            shift 1
            ;;
        --dns-local)
            DNSLOCAL+=($2)
            shift 2
            ;;
        --dns-local-cn)
            DNSLOCAL+=("apple.china.conf")
            DNSLOCAL+=("google.china.conf")
            DNSLOCAL+=("bogus-nxdomain.china.conf")
            DNSLOCAL+=("accelerated-domains.china.conf")
            shift 1
            ;;
        --sub-url)
            SUBURL=$2
            shift 2
            ;;
        --sub-extra-file)
            SUBEXTRAFILE=$2
            shift 2
            ;;
        --sub-extra-vless)
            SUBEXTRAVLESS+=("$2")
            shift 2
            ;;
        --sub-prefer)
            SUBPREFER=$2
            shift 2
            ;;
        --sub-exclude)
            SUBEXCLUDE=$2
            shift 2
            ;;
        --sub-refresh-interval)
            SUBREFRESH=$2
            shift 2
            ;;
        --sub-fetch-mode)
            SUBFETCHMODE=$2
            shift 2
            ;;
        --sub-fetch-proxy)
            SUBFETCHPROXY=$2
            shift 2
            ;;
        --sub-post-start-refresh-delay)
            SUBPOSTSTARTREFRESHDELAY=$2
            shift 2
            ;;
        --sub-check-interval)
            SUBCHECK=$2
            shift 2
            ;;
        --sub-max-failures)
            SUBFAILURES=$2
            shift 2
            ;;
        --sub-degrade-latency)
            SUBDEGRADELATENCY=$2
            shift 2
            ;;
        --sub-degrade-checks)
            SUBDEGRADECHECKS=$2
            shift 2
            ;;
        --sub-retry-interval)
            SUBRETRY=$2
            shift 2
            ;;
        --sub-state-file)
            SUBSTATEFILE=$2
            shift 2
            ;;
        --sub-health-url)
            SUBHEALTHURL=$2
            shift 2
            ;;
        --sub-observatory-probe-interval)
            SUBOBSERVATORYPROBEINTERVAL=$2
            shift 2
            ;;
        --sub-balancer-strategy)
            SUBBALANCERSTRATEGY=$2
            shift 2
            ;;
        --throughput-check-interval)
            THROUGHPUTCHECKINTERVAL=$2
            shift 2
            ;;
        --throughput-url)
            THROUGHPUTURL=$2
            shift 2
            ;;
        --throughput-min-kbps)
            THROUGHPUTMINKBPS=$2
            shift 2
            ;;
        --throughput-max-time)
            THROUGHPUTMAXTIME=$2
            shift 2
            ;;
        --throughput-degrade-checks)
            THROUGHPUTDEGRADECHECKS=$2
            shift 2
            ;;
        --standby-max-age)
            STANDBYMAXAGE=$2
            shift 2
            ;;
        --failover-cooldown)
            FAILOVERCOOLDOWN=$2
            shift 2
            ;;
        --quarantine-duration)
            QUARANTINEDURATION=$2
            shift 2
            ;;
        --candidate-check-min-interval)
            CANDIDATECHECKMININTERVAL=$2
            shift 2
            ;;
        --candidate-check-max-interval)
            CANDIDATECHECKMAXINTERVAL=$2
            shift 2
            ;;
        --candidate-check-timeout)
            CANDIDATECHECKTIMEOUT=$2
            shift 2
            ;;
        --candidate-check-extra-weight)
            CANDIDATECHECKEXTRAWEIGHT=$2
            shift 2
            ;;
        --active-path-interval)
            ACTIVEPATHINTERVAL=$2
            shift 2
            ;;
        --asset-dir)
            ASSETDIR=$2
            shift 2
            ;;
        --asset-refresh-interval)
            ASSETREFRESHINTERVAL=$2
            shift 2
            ;;
        --asset-fetch-timeout)
            ASSETFETCHTIMEOUT=$2
            shift 2
            ;;
        --no-asset-refresh-on-start)
            NOASSETREFRESHONSTART=1
            shift 1
            ;;
        --status-listen)
            STATUSLISTEN=$2
            shift 2
            ;;
        --status-port)
            STATUSPORT=$2
            shift 2
            ;;
        --inbound-vless)
            INBOUND_VLESS=1
            shift 1
            ;;
        --inbound-vless-port)
            INBOUND_VLESS_PORT=$2
            shift 2
            ;;
        --inbound-vless-id)
            INBOUND_VLESS_ID=$2
            shift 2
            ;;
        --inbound-vless-listen)
            INBOUND_VLESS_LISTEN=$2
            shift 2
            ;;
        --telegram-bot-token)
            CLI_TELEGRAM_BOT_TOKEN=$2
            shift 2
            ;;
        --telegram-chat-id)
            CLI_TELEGRAM_CHAT_ID=$2
            shift 2
            ;;
        --cn-direct)
            Jrules=`echo "${Jrules}" | jq --arg igndomain "geosite:apple-cn" \
            '.rules += [{"type":"field","outboundTag":"direct","domain":[$igndomain]}]'`
            Jrules=`echo "${Jrules}" | jq --arg igndomain "geosite:google-cn" \
            '.rules += [{"type":"field","outboundTag":"direct","domain":[$igndomain]}]'`
            Jrules=`echo "${Jrules}" | jq --arg igndomain "geosite:geolocation-cn" \
            '.rules += [{"type":"field","outboundTag":"direct","domain":[$igndomain]}]'`
            Jrules=`echo "${Jrules}" | jq --arg igndomain "geosite:cn" \
            '.rules += [{"type":"field","outboundTag":"direct","domain":[$igndomain]}]'`
            Jrules=`echo "${Jrules}" | jq --arg ignip "geoip:cn" \
            '.rules += [{"type":"field","outboundTag":"direct","ip":[$ignip]}]'`
            shift 1
            ;;
        --domain-direct)
            Jrules=`echo "${Jrules}" | jq --arg igndomain "$2" \
            '.rules += [{"type":"field","outboundTag":"direct","domain":[$igndomain]}]'`
            shift 2
            ;;
        --domain-proxy)
            Jrules=`echo "${Jrules}" | jq --arg pxydomain "$2" \
            '.rules += [{"type":"field","outboundTag":"proxy","domain":[$pxydomain]}]'`
            shift 2
            ;;
        --domain-block)
            Jrules=`echo "${Jrules}" | jq --arg blkdomain "$2" \
            '.rules += [{"type":"field","outboundTag":"block","domain":[$blkdomain]}]'`
            shift 2
            ;;
        --ip-direct)
            Jrules=`echo "${Jrules}" | jq --arg ignip "$2" \
            '.rules += [{"type":"field","outboundTag":"direct","ip":[$ignip]}]'`
            shift 2
            ;;
        --ip-proxy)
            Jrules=`echo "${Jrules}" | jq --arg pxyip "$2" \
            '.rules += [{"type":"field","outboundTag":"proxy","ip":[$pxyip]}]'`
            shift 2
            ;;
        --ip-block)
            Jrules=`echo "${Jrules}" | jq --arg blkip "$2" \
            '.rules += [{"type":"field","outboundTag":"block","ip":[$blkip]}]'`
            shift 2
            ;;
        --rules-path)
            export XRAY_LOCATION_ASSET=$2
            shift 2
            ;;
        -j|--json)
            INJECT+=("$2")
            shift 2
            ;;
        -i|--stdin)
            exec /usr/local/bin/xray
            shift 1
            ;;
        -d|--debug)
            DEBUG=1
            shift 1
            ;;
        --)
            shift
            break
            ;;
        *)
            usage;
            exit 1
            ;;
    esac
done

if [ -z "${PXCMD}" ] && [ -z "${SUBURL}" ]; then >&2 echo -e "Missing Xray connection option.\n"; usage; exit 1; fi
if [ -n "${PXCMD}" ] && [ -n "${SUBURL}" ]; then >&2 echo -e "Use either a connection option or --sub-url, not both.\n"; usage; exit 1; fi

start_dnsmasq() {
    if [ -n "${DNSSPLITRU}" ]; then
        /dns-split-proxy.py --listen 0.0.0.0 --port 53 \
            --dns-global "${DNSGLOBAL}" --dns-ru "${DNSRU}" &
        echo -e "nameserver 127.0.0.1\noptions ndots:0" >/etc/resolv.conf
        return
    fi

    if [ -n "${DNSLOCAL}" ]; then
        for dnslocal in "${DNSLOCAL[@]}"
        do
            cp -a /etc/dnsmasq.disable/${dnslocal} /etc/dnsmasq.d/
        done
    fi
    echo -e "no-resolv\nserver=127.0.0.1#5353" >/etc/dnsmasq.d/upstream.conf
    # Enable external DNS service instead of sereve localy
    sed -i 's/^[[:space:]]*local-service/# &/' /etc/dnsmasq.conf
    /usr/sbin/dnsmasq
    echo -e "nameserver 127.0.0.1\noptions ndots:0" >/etc/resolv.conf
}

if [ -z "${DNSGLOBAL}" ]; then DNSGLOBAL="8.8.8.8"; fi
if [ -z "${DNSRU}" ]; then DNSRU="77.88.8.8"; fi
if [ -z "${DNS}" ]; then DNS="${DNSGLOBAL}"; fi

if [ -n "${SUBURL}" ]; then
    JRULES_FILE=/tmp/proxy-xray-rules.json
    JINJECT_FILE=/tmp/proxy-xray-inject.json
    echo "${Jrules}" >${JRULES_FILE}
    if [ -n "${INJECT}" ]; then
        Jinject='{}'
        for JSON_IN in "${INJECT[@]}"
        do
            Jmerge=`jq -nc "${JSON_IN}"`
            if [[ $? -ne 0 ]]; then echo "Invalid json ${JSON_IN}"; exit 1; fi
            Jinject=`jq -n --argjson Jinject "${Jinject}" --argjson Jmerge "${Jmerge}" '$Jinject + $Jmerge'`
        done
        echo "${Jinject}" >${JINJECT_FILE}
    else
        echo "{}" >${JINJECT_FILE}
    fi
    start_dnsmasq
    SUPERVISOR_ARGS=(--sub-url "${SUBURL}" --dns "${DNS}" --rules-file "${JRULES_FILE}" --inject-file "${JINJECT_FILE}")
    if [ -n "${SUBEXTRAFILE}" ]; then SUPERVISOR_ARGS+=(--extra-file "${SUBEXTRAFILE}"); fi
    if [ -n "${SUBEXTRAVLESS}" ]; then
        for extra_vless in "${SUBEXTRAVLESS[@]}"
        do
            SUPERVISOR_ARGS+=(--extra-vless "${extra_vless}")
        done
    fi
    if [ -n "${SUBPREFER}" ]; then SUPERVISOR_ARGS+=(--prefer "${SUBPREFER}"); fi
    if [ -n "${SUBEXCLUDE}" ]; then SUPERVISOR_ARGS+=(--exclude "${SUBEXCLUDE}"); fi
    if [ -n "${SUBREFRESH}" ]; then SUPERVISOR_ARGS+=(--refresh-interval "${SUBREFRESH}"); fi
    if [ -n "${SUBFETCHMODE}" ]; then SUPERVISOR_ARGS+=(--sub-fetch-mode "${SUBFETCHMODE}"); fi
    if [ -n "${SUBFETCHPROXY}" ]; then SUPERVISOR_ARGS+=(--sub-fetch-proxy "${SUBFETCHPROXY}"); fi
    if [ -n "${SUBPOSTSTARTREFRESHDELAY}" ]; then SUPERVISOR_ARGS+=(--sub-post-start-refresh-delay "${SUBPOSTSTARTREFRESHDELAY}"); fi
    if [ -n "${SUBCHECK}" ]; then SUPERVISOR_ARGS+=(--check-interval "${SUBCHECK}"); fi
    if [ -n "${SUBFAILURES}" ]; then SUPERVISOR_ARGS+=(--max-failures "${SUBFAILURES}"); fi
    if [ -n "${SUBDEGRADELATENCY}" ]; then SUPERVISOR_ARGS+=(--degrade-latency "${SUBDEGRADELATENCY}"); fi
    if [ -n "${SUBDEGRADECHECKS}" ]; then SUPERVISOR_ARGS+=(--degrade-checks "${SUBDEGRADECHECKS}"); fi
    if [ -n "${SUBRETRY}" ]; then SUPERVISOR_ARGS+=(--retry-interval "${SUBRETRY}"); fi
    if [ -n "${SUBSTATEFILE}" ]; then SUPERVISOR_ARGS+=(--state-file "${SUBSTATEFILE}"); fi
    if [ -n "${SUBHEALTHURL}" ]; then SUPERVISOR_ARGS+=(--health-url "${SUBHEALTHURL}"); fi
    if [ -n "${SUBOBSERVATORYPROBEINTERVAL}" ]; then SUPERVISOR_ARGS+=(--observatory-probe-interval "${SUBOBSERVATORYPROBEINTERVAL}"); fi
    if [ -n "${SUBBALANCERSTRATEGY}" ]; then SUPERVISOR_ARGS+=(--balancer-strategy "${SUBBALANCERSTRATEGY}"); fi
    if [ -n "${THROUGHPUTCHECKINTERVAL}" ]; then SUPERVISOR_ARGS+=(--throughput-check-interval "${THROUGHPUTCHECKINTERVAL}"); fi
    if [ -n "${THROUGHPUTURL}" ]; then SUPERVISOR_ARGS+=(--throughput-url "${THROUGHPUTURL}"); fi
    if [ -n "${THROUGHPUTMINKBPS}" ]; then SUPERVISOR_ARGS+=(--throughput-min-kbps "${THROUGHPUTMINKBPS}"); fi
    if [ -n "${THROUGHPUTMAXTIME}" ]; then SUPERVISOR_ARGS+=(--throughput-max-time "${THROUGHPUTMAXTIME}"); fi
    if [ -n "${THROUGHPUTDEGRADECHECKS}" ]; then SUPERVISOR_ARGS+=(--throughput-degrade-checks "${THROUGHPUTDEGRADECHECKS}"); fi
    if [ -n "${STANDBYMAXAGE}" ]; then SUPERVISOR_ARGS+=(--standby-max-age "${STANDBYMAXAGE}"); fi
    if [ -n "${FAILOVERCOOLDOWN}" ]; then SUPERVISOR_ARGS+=(--failover-cooldown "${FAILOVERCOOLDOWN}"); fi
    if [ -n "${QUARANTINEDURATION}" ]; then SUPERVISOR_ARGS+=(--quarantine-duration "${QUARANTINEDURATION}"); fi
    if [ -n "${CANDIDATECHECKMININTERVAL}" ]; then SUPERVISOR_ARGS+=(--candidate-check-min-interval "${CANDIDATECHECKMININTERVAL}"); fi
    if [ -n "${CANDIDATECHECKMAXINTERVAL}" ]; then SUPERVISOR_ARGS+=(--candidate-check-max-interval "${CANDIDATECHECKMAXINTERVAL}"); fi
    if [ -n "${CANDIDATECHECKTIMEOUT}" ]; then SUPERVISOR_ARGS+=(--candidate-check-timeout "${CANDIDATECHECKTIMEOUT}"); fi
    if [ -n "${CANDIDATECHECKEXTRAWEIGHT}" ]; then SUPERVISOR_ARGS+=(--candidate-check-extra-weight "${CANDIDATECHECKEXTRAWEIGHT}"); fi
    if [ -n "${ACTIVEPATHINTERVAL}" ]; then SUPERVISOR_ARGS+=(--active-path-interval "${ACTIVEPATHINTERVAL}"); fi
    if [ -n "${ASSETDIR}" ]; then SUPERVISOR_ARGS+=(--asset-dir "${ASSETDIR}"); fi
    if [ -n "${ASSETREFRESHINTERVAL}" ]; then SUPERVISOR_ARGS+=(--asset-refresh-interval "${ASSETREFRESHINTERVAL}"); fi
    if [ -n "${ASSETFETCHTIMEOUT}" ]; then SUPERVISOR_ARGS+=(--asset-fetch-timeout "${ASSETFETCHTIMEOUT}"); fi
    if [ -n "${NOASSETREFRESHONSTART}" ]; then SUPERVISOR_ARGS+=(--no-asset-refresh-on-start); fi
    if [ -n "${STATUSLISTEN}" ]; then SUPERVISOR_ARGS+=(--status-listen "${STATUSLISTEN}"); fi
    if [ -n "${STATUSPORT}" ]; then SUPERVISOR_ARGS+=(--status-port "${STATUSPORT}"); fi
    if [ -n "${INBOUND_VLESS}" ]; then SUPERVISOR_ARGS+=(--inbound-vless); fi
    if [ -n "${INBOUND_VLESS_PORT}" ]; then SUPERVISOR_ARGS+=(--inbound-vless-port "${INBOUND_VLESS_PORT}"); fi
    if [ -n "${INBOUND_VLESS_ID}" ]; then SUPERVISOR_ARGS+=(--inbound-vless-id "${INBOUND_VLESS_ID}"); fi
    if [ -n "${INBOUND_VLESS_LISTEN}" ]; then SUPERVISOR_ARGS+=(--inbound-vless-listen "${INBOUND_VLESS_LISTEN}"); fi
    if [ -n "${CLI_TELEGRAM_BOT_TOKEN}" ]; then SUPERVISOR_ARGS+=(--telegram-bot-token "${CLI_TELEGRAM_BOT_TOKEN}"); fi
    if [ -n "${CLI_TELEGRAM_CHAT_ID}" ]; then SUPERVISOR_ARGS+=(--telegram-chat-id "${CLI_TELEGRAM_CHAT_ID}"); fi
    if [ -n "${DEBUG}" ]; then SUPERVISOR_ARGS+=(--debug); fi
    exec python3 /subscription-supervisor.py "${SUPERVISOR_ARGS[@]}"
fi

# Add outbounds config
Joutbound=`$PXCMD`
if [ $? != 0 ]; then >&2 echo -e "${subcmd} Config failed: $PXCMD\n"; exit 2; fi
# First outbound will be the DEFAULT
Jroot=`jq -nc --argjson Joutbound "${Joutbound}" '.outbounds += [$Joutbound]'`
Jroot=`echo $Jroot|jq '.outbounds += [{"tag":"direct","protocol":"freedom"},{"tag":"block","protocol":"blackhole"},{"tag":"blocked","protocol":"blackhole"}]'`

# Add inbounds config
JibDNS=`jq -nc --arg dns "${DNS}" \
'. +={"tag":"dns-in","port":5353,"listen":"0.0.0.0","protocol":"dokodemo-door","settings":{"address":$dns,"port":53,"network":"tcp,udp"}}'`
JibSOCKS=`jq -nc '. +={"tag":"socks","port":1080,"listen":"0.0.0.0","protocol":"socks","settings":{"udp":true}}'`
JibHTTP=`jq -nc '. +={"tag":"http","port":8123,"listen":"0.0.0.0","protocol":"http"}'`
Jroot=`echo $Jroot|jq --argjson JibDNS "${JibDNS}" --argjson JibSOCKS "${JibSOCKS}" --argjson JibHTTP "${JibHTTP}" \
'.inbounds += [$JibDNS,$JibSOCKS,$JibHTTP]'`

# Add routing config
Jrouting='{"routing":{"domainStrategy":"AsIs"}}'
Jrouting=`echo "${Jrouting}" |jq --argjson Jrules "${Jrules}" '.routing += $Jrules'`
Jroot=`echo $Jroot|jq --argjson Jrouting "${Jrouting}" '. += $Jrouting'`

# Add debug config
if [ -n "${DEBUG}" ]; then loglevel="debug"; else loglevel="warning"; fi
Jroot=`echo $Jroot| jq --arg loglevel "${loglevel}" '.log.loglevel |= $loglevel'`

# Merge injected json config
if [ -n "${INJECT}" ]; then
    for JSON_IN in "${INJECT[@]}"
    do
        Jmerge=`jq -nc "${JSON_IN}"`
        if [[ $? -ne 0 ]]; then echo "Invalid json ${JSON_IN}"; exit 1; fi
        Jroot=`jq -n --argjson Jroot "${Jroot}" --argjson Jmerge "${Jmerge}" '$Jroot + $Jmerge'`
    done
fi

# Add Dnsmasq config
start_dnsmasq

jq -n "$Jroot"
jq -n "$Jroot">$XCONF
/qrcode
exec /usr/local/bin/xray -c $XCONF
