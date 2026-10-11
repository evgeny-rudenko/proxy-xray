"""Native Xray routing graph: public egress -> exit transport -> relay."""
import copy

from .routes import DIRECT, CHAIN, SUBSCRIPTION
from .xray_config import make_inbounds

DNS_PROXY_PORT = 10953
SLOTS = (
    {"socks_port": 11180, "http_port": 18123, "vless_port": 11186, "api_port": 11185,
     "dns_port": 11184, "probe_ports": {DIRECT: 11181, CHAIN: 11182, SUBSCRIPTION: 11183}},
    {"socks_port": 12180, "http_port": 19123, "vless_port": 12186, "api_port": 12185,
     "dns_port": 12184, "probe_ports": {DIRECT: 12181, CHAIN: 12182, SUBSCRIPTION: 12183}},
)
BALANCERS = {DIRECT: "amsterdam-direct", CHAIN: "amsterdam-chain", SUBSCRIPTION: "relays"}


def tagged(candidate, prefix):
    result = copy.deepcopy(candidate["outbound"])
    result["tag"] = prefix + candidate["tag"]
    return result


def balancer(tag, prefix, fallback="block"):
    return {"tag": tag, "selector": prefix if isinstance(prefix, list) else [prefix],
            "fallbackTag": fallback, "strategy": {"type": "leastPing"}}


def relay_tag(candidate):
    return "relay-" + candidate["tag"] + "-hop"


def chain_relay_balancer(candidate):
    return "amsterdam-relay-" + candidate["tag"]


def make_chain_config(exits, relays, args, slot, confirmed_direct=(), confirmed_pairs=()):
    outbounds, mapping = [], {}
    for c in exits:
        direct = tagged(c, "exit-direct-")
        chained = tagged(c, "exit-chain-")
        chained.setdefault("streamSettings", {}).setdefault("sockopt", {})["dialerProxy"] = "relay-loop-" + c["tag"]
        outbounds.extend((direct, chained))
        mapping[direct["tag"]] = c
        mapping[chained["tag"]] = c
    for c in relays:
        item = tagged(c, "relay-")
        item["tag"] = relay_tag(c)
        outbounds.append(item)
        mapping[item["tag"]] = c
    for tag, inbound in (("route-direct", "stage-direct"), ("route-chain", "stage-chain"),
                         ("route-subscription", "stage-relay")):
        outbounds.append({"tag": tag, "protocol": "loopback", "settings": {"inboundTag": inbound}})
    pair_balancers = []
    for c in exits:
        outbounds.append({"tag": "relay-loop-" + c["tag"], "protocol": "loopback",
                         "settings": {"inboundTag": "stage-relay-" + c["tag"]}})
        allowed = [relay_tag(r) for r in relays if (c["uri"], r["uri"]) in confirmed_pairs]
        pair_balancers.append(balancer(chain_relay_balancer(c), allowed or ["no-confirmed-pair-" + c["tag"]],
                                      allowed[0] if allowed else "block"))
    outbounds.extend([{"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}])
    inbounds = make_inbounds("127.0.0.1", args, socks_port=slot["socks_port"],
                             http_port=slot["http_port"], dns_port=slot["dns_port"],
                             api_port=slot["api_port"], inbound_vless_port=slot["vless_port"])
    inbounds = [item for item in inbounds if item["tag"] != "dns-in"]
    for item in inbounds:
        item["listen"] = "127.0.0.1"
    inbounds.append({"tag": "global-dns", "listen": "127.0.0.1", "port": slot["dns_port"],
                     "protocol": "socks", "settings": {"udp": False}})
    for mode, port in slot["probe_ports"].items():
        inbounds.append({"tag": "probe-" + mode, "listen": "127.0.0.1", "port": port,
                         "protocol": "socks", "settings": {"udp": False}})
    rules = [{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}]
    for inbound, target in (("stage-direct", "amsterdam-direct"), ("stage-chain", "amsterdam-chain"),
                            ("stage-relay", "relays")):
        rules.append({"type": "field", "inboundTag": [inbound], "balancerTag": target})
    for c in exits:
        rules.append({"type": "field", "inboundTag": ["stage-relay-" + c["tag"]],
                      "balancerTag": chain_relay_balancer(c)})
    for mode, target in BALANCERS.items():
        rules.append({"type": "field", "inboundTag": ["probe-" + mode], "balancerTag": target})
    # DNS must use the selected foreign path even if a resolver's IP matches a RU rule.
    rules.append({"type": "field", "inboundTag": ["global-dns"], "balancerTag": "egress"})
    if args.direct_ru:
        rules.extend([
            {"type": "field", "domain": ["geosite:category-ru", "domain:ru", "domain:su", "domain:xn--p1ai"],
             "outboundTag": "direct"},
            {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"},
        ])
    rules.append({"type": "field", "network": "tcp,udp", "balancerTag": "egress"})
    direct_fallback = next(("exit-direct-" + c["tag"] for c in exits if c["uri"] in confirmed_direct), "block")
    relay_fallback = relay_tag(relays[0]) if relays else "block"
    chain_fallback = next(("exit-chain-" + c["tag"] for c in exits
                           if any((c["uri"], r["uri"]) in confirmed_pairs for r in relays)), "block")
    config = {
        "log": {"loglevel": "warning"},
        "api": {"tag": "api", "services": ["RoutingService"]},
        "dns": {"servers": ["127.0.0.1"], "queryStrategy": "UseIPv4"},
        "inbounds": inbounds, "outbounds": outbounds,
        "observatory": {"subjectSelector": ["exit-direct-", "exit-chain-", "relay-proxy-"],
                        "probeUrl": args.health_url, "probeInterval": args.observatory_probe_interval,
                        "enableConcurrency": False},
        "routing": {"domainStrategy": "IPIfNonMatch", "rules": rules, "balancers": [
            balancer("amsterdam-direct", "exit-direct-", direct_fallback),
            balancer("amsterdam-chain", "exit-chain-", chain_fallback),
            balancer("relays", "relay-proxy-", relay_fallback),
            *pair_balancers,
            balancer("egress", "route-", "block"),
        ]},
    }
    return config, mapping

