#!/usr/bin/env python3
import os
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from proxy_xray import status
from proxy_xray.status_server import render_servers_html, render_status_html


def candidate(tag, source, name, host, port, network, security, score, latency, throughput, ok_offset):
    now = time.time()
    return {
        "tag": tag,
        "source": source,
        "name": name,
        "host": host,
        "port": port,
        "network": network,
        "security": security,
        "last_latency": latency,
        "last_throughput_kbps": throughput,
        "last_ok_at": now - ok_offset,
        "last_fail_at": None,
        "quarantine_until": None,
        "quarantine_reason": None,
        "fallback_score": score,
        "fallback_score_reasons": ["recent ok", "low latency"] if score >= 80 else ["recent ok"],
    }


def main():
    now = time.time()
    live = [
        candidate("proxy-extra-0", "extra", "Private Europe A", "edge-a.example.net", 443, "tcp", "reality", 96, 0.182, 42800, 72),
        candidate("proxy-extra-1", "extra", "Private Europe B", "edge-b.example.net", 8443, "grpc", "tls", 91, 0.244, 38600, 185),
        candidate("proxy-sub-4", "subscription", "Germany 12", "de-12.example.org", 443, "ws", "tls", 84, 0.512, 27400, 340),
        candidate("proxy-sub-9", "subscription", "Netherlands 07", "nl-07.example.org", 443, "tcp", "reality", 78, 0.691, 21900, 470),
        candidate("proxy-sub-15", "subscription", "Finland 03", "fi-03.example.org", 443, "ws", "tls", 66, 1.104, 12800, 830),
    ]
    all_candidates = live + [
        {
            **candidate("proxy-sub-21", "subscription", "France 18", "fr-18.example.org", 443, "tcp", "reality", 38, None, None, 0),
            "last_ok_at": None,
            "last_fail_at": now - 520,
            "fallback_score_reasons": ["last check failed"],
        },
        {
            **candidate("proxy-sub-22", "subscription", "Spain 05", "es-05.example.org", 443, "grpc", "tls", 32, None, None, 0),
            "last_ok_at": None,
            "last_fail_at": now - 780,
            "fallback_score_reasons": ["last check failed"],
        },
    ]

    health_checks = {
        "xray_process": {"status": "ok", "label": "Xray process", "detail": "running"},
        "socks_proxy": {"status": "ok", "label": "SOCKS proxy", "detail": "generate_204", "latency": 0.214},
        "http_proxy": {"status": "ok", "label": "HTTP proxy", "detail": "HTTP 204", "latency": 0.236},
        "lan_vless": {"status": "ok", "label": "LAN VLESS inbound", "detail": "127.0.0.1:10086", "latency": 0.001},
        "dns_ru": {"status": "ok", "label": "RU DNS", "detail": "yandex.ru", "latency": 0.041},
        "dns_global": {"status": "ok", "label": "Global DNS", "detail": "google.com", "latency": 0.022},
        "telegram": {"status": "ok", "label": "Telegram API", "detail": "reachable without sending message"},
        "throughput": {"status": "ok", "label": "Throughput", "detail": "42.8 Mbps"},
        "subscription": {"status": "ok", "label": "Subscription", "detail": "loaded 64 subscription links via proxy"},
        "direct_internet": {"status": "ok", "label": "Direct internet", "detail": "HTTP 204", "latency": 0.055},
    }

    status.STATUS.update(
        {
            "started_at": now - 18530,
            "mode": "native-balancer",
            "xray_running": True,
            "candidates_count": len(all_candidates),
            "sources": {"extra": 2, "subscription": 5},
            "fallback": live[0],
            "active_path": {
                "status": "ok",
                "balancer": "auto",
                "strategy": "leastPing",
                "selected_tag": "proxy-extra-0",
                "selected": live[0],
                "fallback": live[0],
            },
            "active_backend": {"running": True, "candidate": live[0], "socks_port": 11080, "http_port": 18123},
            "hot_standby": {"running": True, "healthy": True, "candidate": live[1], "socks_port": 12080, "http_port": 18223},
            "last_refresh_at": now - 480,
            "last_subscription_success_at": now - 1220,
            "last_health": {"status": "ok", "latency": 0.214},
            "last_throughput": {"status": "ok", "kbps": 42800},
            "health_checks": health_checks,
            "last_health_checks_at": now - 8,
            "last_subscription_status": {"status": "ok", "detail": "loaded 64 subscription links via proxy"},
            "subscription_fetch": {"mode": "auto", "proxy": "socks5h://127.0.0.1:1080", "proxy_allowed": True, "last_method": "proxy"},
            "assets": {
                "dir": "/opt/proxy-xray/assets",
                "refresh_interval": 86400,
                "last_check_at": now - 5600,
                "last_success_at": now - 5600,
                "last_status": {"status": "ok", "reason": "scheduled", "detail": "assets refreshed", "time": now - 5600},
                "items": {
                    "geoip": {"status": "ok", "size": 18863677, "mtime": now - 5600, "last_success_at": now - 5600, "last_error": None},
                    "geosite": {"status": "ok", "size": 10397605, "mtime": now - 5600, "last_success_at": now - 5600, "last_error": None},
                    "iran": {"status": "ok", "size": 3705659, "mtime": now - 86400, "last_success_at": None, "last_error": None},
                },
            },
            "last_candidate_check": {"status": "ok", "tag": "proxy-sub-9", "latency": 0.691},
            "next_candidate_check_at": now + 184,
            "candidate_checker": {"enabled": True, "min_interval": 120, "max_interval": 300},
            "standby": live[1],
            "standby_policy": {"max_age": 600, "cooldown": 180, "quarantine_duration": 900},
            "switch_cooldown_until": None,
            "last_switch": {"time": now - 7800, "reason": "throughput degraded", "from": "proxy-sub-2", "to": "proxy-extra-0"},
            "quarantine_count": 1,
            "failures": 0,
            "slow_checks": 0,
            "throughput_slow_checks": 0,
            "candidates": all_candidates,
            "tested_live_candidates": live,
            "routing": {
                "direct_domains": ["geosite:category-ru", "regexp:.*\\.ru$", "regexp:.*\\.su$", "regexp:.*\\.xn--p1ai$"],
                "direct_ips": ["geoip:ru"],
                "balancers": ["auto"],
            },
        }
    )
    status.LOG_BUFFER.clear()
    for offset, line in [
        (460, "refreshing subscription"),
        (454, "loaded 64 subscription links, 2 extra links, 66 candidates after filtering"),
        (420, "active-a active selected 1 candidates; fallback: proxy-extra-0 (edge-a.example.net:443); socks=11080 http=18123"),
        (392, "active-b hot standby selected 1 candidates; fallback: proxy-extra-1 (edge-b.example.net:8443); socks=12080 http=18223"),
        (300, "hot standby ok proxy-extra-1 (0.244s)"),
        (228, "throughput ok: 42800 kbps"),
        (184, "checking weighted-random candidate proxy-sub-9 (nl-07.example.org:443)"),
        (178, "candidate ok proxy-sub-9: 0.691s"),
        (8, "health checks ok; public ports remain on proxy-extra-0"),
    ]:
        status.LOG_BUFFER.append({"time": now - offset, "line": f"[sub] {line}"})

    os.makedirs("docs", exist_ok=True)
    with open("docs/status-dashboard-demo.html", "wb") as handle:
        handle.write(render_status_html())
    with open("docs/status-servers-demo.html", "wb") as handle:
        handle.write(render_servers_html("live"))


if __name__ == "__main__":
    main()
