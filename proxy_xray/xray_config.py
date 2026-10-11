import json

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


def config_fingerprint(config):
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def write_config(path, config):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, separators=(",", ":"))
