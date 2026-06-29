#!/usr/bin/env python3
import os
import sys
import time

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from proxy_xray import status
from proxy_xray.status_server import render_dashboard_v5_html, render_servers_html


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
        candidate("proxy-sub-0", "subscription", "Czech Republic", "cz-edge.example.net", 443, "tcp", "reality", 1525.803, 0.353, 32400, 34),
        candidate("proxy-sub-1", "subscription", "Germany Multi", "de-multi.example.net", 443, "tcp", "reality", 1493.665, 0.327, 36100, 75),
        candidate("proxy-sub-2", "subscription", "Poland", "pl-edge.example.net", 443, "tcp", "reality", 1455.166, 0.334, 28900, 136),
        candidate("proxy-sub-3", "subscription", "Finland", "fi-edge.example.net", 443, "tcp", "reality", 1428.766, 0.543, 24100, 210),
        candidate("proxy-sub-4", "subscription", "Netherlands Multi", "nl-multi.example.net", 443, "tcp", "reality", 1428.407, 0.438, 31200, 330),
        candidate("proxy-sub-5", "subscription", "Italy", "it-edge.example.net", 443, "tcp", "reality", 1424.021, 0.509, 21800, 420),
        candidate("proxy-sub-6", "subscription", "Norway", "no-edge.example.net", 443, "tcp", "reality", 1417.387, 0.682, 19700, 520),
        candidate("proxy-extra-0", "extra", "Private TCP TLS", "private-a.example.net", 8443, "tcp", "tls", 1388.347, 0.335, 29200, 610),
        candidate("proxy-extra-1", "extra", "Private Reality", "private-b.example.net", 443, "tcp", "reality", 1330.615, 0.270, 33100, 840),
        candidate("proxy-sub-25", "subscription", "Ukraine", "ua-edge.example.net", 443, "tcp", "reality", 1272.611, 0.513, 18600, 1000),
        candidate("proxy-sub-42", "subscription", "Germany Extra", "de-extra.example.net", 443, "tcp", "reality", 1212.078, 0.310, 23000, 26),
    ]
    current = live[-1]
    hot = live[0]
    active_pool = [current, live[1], live[7]]
    standby_pool = [hot, live[8], live[9]]
    generated_live = [
        candidate(
            f"proxy-sub-{index}",
            "subscription",
            f"Europe Node {index}",
            f"eu-{index:02d}.example.net",
            443,
            "tcp",
            "reality",
            1260 - index,
            0.350 + (index % 9) / 100,
            18000 + index * 120,
            1200 + index * 17,
        )
        for index in range(10, 54)
    ]
    tested_live = live[:10] + generated_live
    all_candidates = tested_live + [
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
        "quality_download": {"status": "ok", "label": "Quality download", "detail": "5983 kbps"},
        "throughput": {"status": "ok", "label": "Throughput", "detail": "23009 kbps"},
        "subscription": {"status": "ok", "label": "Subscription", "detail": "loaded 60 subscription links via proxy"},
        "direct_internet": {"status": "ok", "label": "Direct internet", "detail": "HTTP 204", "latency": 0.055},
    }

    status.STATUS.update(
        {
            "started_at": now - 18530,
            "mode": "native-balancer",
            "xray_running": True,
            "candidates_count": 62,
            "sources": {"extra": 4, "subscription": 58},
            "fallback": current,
            "active_path": {
                "status": "ok",
                "balancer": "auto",
                "strategy": "leastPing",
                "selected_tag": current["tag"],
                "selected": current,
                "fallback": current,
                },
            "active_backend": {"running": True, "candidate": current, "pool_size": 3, "socks_port": 11080, "http_port": 18123},
            "hot_standby": {"running": True, "healthy": True, "candidate": hot, "pool_size": 3, "socks_port": 12080, "http_port": 18223},
            "active_pool": active_pool,
            "standby_pool": standby_pool,
            "last_refresh_at": now - 480,
            "last_subscription_success_at": now - 1220,
            "last_health": {"status": "ok", "latency": 0.214},
            "last_throughput": {"status": "ok", "kbps": 23009},
            "health_checks": health_checks,
            "last_health_checks_at": now - 8,
            "last_subscription_status": {"status": "ok", "detail": "loaded 60 subscription links via proxy"},
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
                },
            },
            "last_candidate_check": {"status": "ok", "tag": current["tag"], "latency": current["last_latency"]},
            "next_candidate_check_at": now + 184,
            "candidate_checker": {"enabled": True, "min_interval": 120, "max_interval": 300},
            "standby": hot,
            "standby_policy": {"max_age": 600, "cooldown": 180, "quarantine_duration": 900},
            "switch_cooldown_until": None,
            "last_switch": {"time": now - 7800, "reason": "throughput degraded", "from": "proxy-sub-2", "to": "proxy-extra-0"},
            "quarantine_count": 1,
            "failures": 0,
            "slow_checks": 0,
            "throughput_slow_checks": 0,
            "candidates": all_candidates,
            "tested_live_candidates": tested_live,
            "routing": {
                "direct_domains": ["geosite:category-ru", "regexp:.*\\.ru$", "regexp:.*\\.su$", "regexp:.*\\.xn--p1ai$"],
                "direct_ips": ["geoip:ru"],
                "balancers": ["auto"],
            },
        }
    )
    status.LOG_BUFFER.clear()
    for offset, line in [
        (720, "loaded 60 subscription links, 4 extra links, 62 candidates after filtering"),
        (700, "hot standby ok proxy-sub-0, proxy-extra-1, proxy-sub-25 (0.379s)"),
        (680, "quality download ok: 6996 kbps"),
        (560, "hot standby ok proxy-sub-0, proxy-extra-1, proxy-sub-25 (0.408s)"),
        (440, "hot standby ok proxy-sub-0, proxy-extra-1, proxy-sub-25 (0.425s)"),
        (320, "quality download ok: 5983 kbps"),
        (210, "throughput ok: 23009 kbps"),
        (48, "hot standby ok proxy-sub-0, proxy-extra-1, proxy-sub-25 (0.353s)"),
        (8, "quality download ok: 6823 kbps"),
    ]:
        status.LOG_BUFFER.append({"time": now - offset, "line": f"[sub] {line}"})

    os.makedirs("docs", exist_ok=True)
    with open("docs/status-dashboard-demo.html", "wb") as handle:
        handle.write(render_dashboard_v5_html())
    with open("docs/status-servers-demo.html", "wb") as handle:
        handle.write(render_servers_html("live"))


if __name__ == "__main__":
    main()
