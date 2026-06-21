import json

from .util import shallow_merge
from .vless import assign_candidate_tags


XRAY_API_PORT = 10085


ROUTE_SNIFFING = {
    "enabled": True,
    "destOverride": ["http", "tls", "quic"],
    "routeOnly": True,
}


def make_inbounds(
    dns,
    args,
    socks_port=1080,
    http_port=8123,
    dns_port=5353,
    api_port=XRAY_API_PORT,
    inbound_vless_port=None,
):
    inbounds = [
        {
            "tag": "api",
            "port": api_port,
            "listen": "127.0.0.1",
            "protocol": "dokodemo-door",
            "settings": {"address": "127.0.0.1"},
        },
        {
            "tag": "dns-in",
            "port": dns_port,
            "listen": "0.0.0.0",
            "protocol": "dokodemo-door",
            "settings": {"address": dns, "port": 53, "network": "tcp,udp"},
        },
        {
            "tag": "socks",
            "port": socks_port,
            "listen": "0.0.0.0",
            "protocol": "socks",
            "settings": {"udp": True},
            "sniffing": ROUTE_SNIFFING,
        },
        {
            "tag": "http",
            "port": http_port,
            "listen": "0.0.0.0",
            "protocol": "http",
            "sniffing": ROUTE_SNIFFING,
        },
    ]
    if args.inbound_vless:
        vless_port = inbound_vless_port or args.inbound_vless_port
        inbounds.append(
            {
                "tag": "vless-in",
                "port": vless_port,
                "listen": args.inbound_vless_listen,
                "protocol": "vless",
                "settings": {
                    "clients": [
                        {
                            "id": args.inbound_vless_id,
                            "level": 0,
                        }
                    ],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "none",
                },
                "sniffing": ROUTE_SNIFFING,
            }
        )
    return inbounds


def rules_for_native_balancer(rules, balancer_tag):
    converted = [{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}]
    for rule in rules.get("rules", []):
        next_rule = dict(rule)
        if next_rule.get("outboundTag") == "proxy":
            next_rule.pop("outboundTag", None)
            next_rule["balancerTag"] = balancer_tag
        converted.append(next_rule)
    converted.append({"type": "field", "network": "tcp,udp", "balancerTag": balancer_tag})
    return converted


def make_native_balancer_config(
    candidates,
    dns,
    rules,
    inject,
    args,
    socks_port=1080,
    http_port=8123,
    dns_port=5353,
    api_port=XRAY_API_PORT,
    inbound_vless_port=None,
):
    assign_candidate_tags(candidates)
    proxy_outbounds = [candidate["outbound"] for candidate in candidates]
    fallback = candidates[0]["tag"]
    balancer_tag = "auto"
    config = {
        "log": {"loglevel": "warning"},
        "api": {
            "tag": "api",
            "services": ["RoutingService"],
        },
        "observatory": {
            "subjectSelector": ["proxy-extra-", "proxy-sub-"],
            "probeUrl": args.health_url,
            "probeInterval": args.observatory_probe_interval,
            "enableConcurrency": False,
        },
        "outbounds": proxy_outbounds
        + [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
        "inbounds": make_inbounds(
            dns,
            args,
            socks_port=socks_port,
            http_port=http_port,
            dns_port=dns_port,
            api_port=api_port,
            inbound_vless_port=inbound_vless_port,
        ),
        "routing": {
            "domainStrategy": "AsIs",
            "rules": rules_for_native_balancer(rules, balancer_tag),
            "balancers": [
                {
                    "tag": balancer_tag,
                    "selector": ["proxy-extra-", "proxy-sub-"],
                    "fallbackTag": fallback,
                    "strategy": {"type": args.balancer_strategy},
                }
            ],
        },
    }
    return shallow_merge(config, inject)


def config_fingerprint(config):
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def write_config(path, config):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, separators=(",", ":"))
