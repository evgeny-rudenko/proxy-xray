import re
import time
from urllib.parse import urlparse

from .health import curl_probe, dns_probe
from .status import status_snapshot


SECRET_PATTERNS = (
    re.compile(r"vless://[^ \n\r\t\"']+", re.IGNORECASE),
    re.compile(r"https?://[^ \n\r\t\"']*/sub/[A-Za-z0-9_.~%-]+", re.IGNORECASE),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
)


def redact_value(value):
    if isinstance(value, str):
        redacted = value
        for pattern in SECRET_PATTERNS:
            redacted = pattern.sub("<redacted>", redacted)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}
    return value


def split_csv(value):
    if not value:
        return []
    items = []
    for part in str(value).split(","):
        item = part.strip()
        if item:
            items.append(item)
    return items


def diagnostic_urls(args):
    urls = []
    for configured in getattr(args, "diagnostic_url", None) or []:
        urls.extend(split_csv(configured))
    if urls:
        return urls
    return [
        getattr(args, "health_url", "https://www.gstatic.com/generate_204"),
        getattr(args, "quality_url", "https://speed.cloudflare.com/__down?bytes=512000"),
        "https://pikabu.ru/",
    ]


def probe_expected_codes(url):
    parsed = urlparse(url)
    if parsed.netloc.endswith("gstatic.com"):
        return {"204"}
    return {str(code) for code in range(200, 400)}


def probe_url(url, args):
    timeout = getattr(args, "diagnostic_probe_timeout", None) or getattr(args, "diagnostics_timeout", 4)
    expected = probe_expected_codes(url)
    return {
        "url": redact_value(url),
        "host": urlparse(url).netloc,
        "direct": curl_probe("direct", url, timeout, expected_codes=expected),
        "socks": curl_probe("socks", url, timeout, proxy="socks5h://127.0.0.1:1080", expected_codes=expected),
        "http": curl_probe("http", url, timeout, proxy="http://127.0.0.1:8123", expected_codes=expected),
    }


def build_dns_diagnostics(args):
    timeout = getattr(args, "diagnostic_probe_timeout", None) or getattr(args, "diagnostics_timeout", 4)
    return {
        "ru": dns_probe("RU DNS", "yandex.ru", "127.0.0.1", timeout),
        "global": dns_probe("Global DNS", "google.com", "127.0.0.1", timeout),
    }


def compact_candidate(candidate):
    if not candidate:
        return None
    return {
        "tag": candidate.get("tag"),
        "source": candidate.get("source"),
        "endpoint": f"{candidate.get('host') or '-'}:{candidate.get('port') or '-'}",
        "transport": f"{candidate.get('network') or '-'}/{candidate.get('security') or '-'}",
        "last_latency": candidate.get("last_latency"),
        "last_ok_at": candidate.get("last_ok_at"),
        "score": candidate.get("fallback_score"),
    }


def build_diagnostics(args):
    snapshot = status_snapshot()
    active_backend = snapshot.get("active_backend") or {}
    hot_standby = snapshot.get("hot_standby") or {}
    active_path = snapshot.get("active_path") or {}
    urls = diagnostic_urls(args)
    data = {
        "generated_at": time.time(),
        "summary": {
            "mode": snapshot.get("mode"),
            "xray_running": snapshot.get("xray_running"),
            "failover_state": snapshot.get("failover_state"),
            "last_health": snapshot.get("last_health"),
            "last_quality": snapshot.get("last_quality"),
            "last_throughput": snapshot.get("last_throughput"),
            "last_switch": snapshot.get("last_switch"),
            "subscription": snapshot.get("last_subscription_status"),
            "assets": snapshot.get("assets"),
        },
        "active": {
            "slot": active_backend.get("name"),
            "running": active_backend.get("running"),
            "healthy": active_backend.get("healthy"),
            "selected_tag": active_path.get("selected_tag"),
            "selected": compact_candidate(active_path.get("selected") or active_backend.get("candidate")),
            "pool_size": active_backend.get("pool_size") or len(snapshot.get("active_pool") or []),
        },
        "standby": {
            "slot": hot_standby.get("name"),
            "running": hot_standby.get("running"),
            "healthy": hot_standby.get("healthy"),
            "selected": compact_candidate(hot_standby.get("candidate")),
            "pool_size": hot_standby.get("pool_size") or len(snapshot.get("standby_pool") or []),
        },
        "dns": build_dns_diagnostics(args),
        "probes": [probe_url(url, args) for url in urls],
    }
    return redact_value(data)
