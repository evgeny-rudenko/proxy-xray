from collections import Counter, deque
import json
import threading
import time


LOG_BUFFER = deque(maxlen=500)
STATUS_LOCK = threading.Lock()
DEFAULT_HEALTH_CHECKS = {
    "xray_process": {"status": "unknown", "label": "Xray process", "detail": "not checked yet"},
    "socks_proxy": {"status": "unknown", "label": "SOCKS proxy", "detail": "not checked yet"},
    "http_proxy": {"status": "unknown", "label": "HTTP proxy", "detail": "not checked yet"},
    "lan_vless": {"status": "unknown", "label": "LAN VLESS inbound", "detail": "not checked yet"},
    "quality_download": {"status": "unknown", "label": "Quality download", "detail": "not checked yet"},
    "throughput": {"status": "unknown", "label": "Throughput", "detail": "not checked yet"},
    "direct_internet": {"status": "unknown", "label": "Direct internet", "detail": "not checked yet"},
    "subscription": {"status": "unknown", "label": "Subscription", "detail": "not checked yet"},
    "dns_ru": {"status": "unknown", "label": "RU DNS", "detail": "not checked yet"},
    "dns_global": {"status": "unknown", "label": "Global DNS", "detail": "not checked yet"},
    "telegram": {"status": "unknown", "label": "Telegram", "detail": "not checked yet"},
}
STATUS = {
    "started_at": time.time(),
    "mode": "native-balancer",
    "xray_running": False,
    "candidates_count": 0,
    "sources": {},
    "active_pool": [],
    "active_path": None,
    "active_observatory": None,
    "active_backend": None,
    "hot_standby": None,
    "standby_pool": [],
    "standby_observatory": None,
    "last_refresh_at": None,
    "last_subscription_success_at": None,
    "last_health": None,
    "last_quality": None,
    "last_throughput": None,
    "health_checks": DEFAULT_HEALTH_CHECKS,
    "last_health_checks_at": None,
    "last_subscription_status": None,
    "subscription_fetch": {"mode": None, "proxy": None, "proxy_allowed": False, "last_method": None},
    "assets": None,
    "last_candidate_check": None,
    "next_candidate_check_at": None,
    "candidate_checker": {"enabled": False, "min_interval": None, "max_interval": None},
    "standby_policy": {"max_age": None, "cooldown": None, "quarantine_duration": None},
    "switch_cooldown_until": None,
    "failover_state": {
        "state": "idle",
        "kind": "none",
        "reason": None,
        "full_failure": False,
        "standby_ready": False,
        "cooldown_remaining": 0,
    },
    "last_switch": None,
    "quarantine_count": 0,
    "failures": 0,
    "slow_checks": 0,
    "quality_slow_checks": 0,
    "throughput_slow_checks": 0,
    "last_event": None,
    "candidates": [],
    "tested_live_candidates": [],
    "routing": {"direct_domains": [], "direct_ips": [], "balancers": []},
}


def log(message):
    line = f"[sub] {message}"
    LOG_BUFFER.append({"time": time.time(), "line": line})
    with STATUS_LOCK:
        STATUS["last_event"] = {"time": time.time(), "message": message}
    print(line, flush=True)


def set_status(**kwargs):
    with STATUS_LOCK:
        STATUS.update(kwargs)


def public_candidate(candidate):
    stream = candidate.get("outbound", {}).get("streamSettings", {})
    return {
        "tag": candidate.get("tag"),
        "name": candidate.get("name"),
        "host": candidate.get("host"),
        "port": candidate.get("port"),
        "source": candidate.get("source"),
        "network": stream.get("network"),
        "security": stream.get("security"),
        "last_latency": candidate.get("last_latency"),
        "last_throughput_kbps": candidate.get("last_throughput_kbps"),
        "last_ok_at": candidate.get("last_ok_at"),
        "last_fail_at": candidate.get("last_fail_at"),
        "last_xray_selected_at": candidate.get("last_xray_selected_at"),
        "last_xray_selected_slot": candidate.get("last_xray_selected_slot"),
        "quarantine_until": candidate.get("quarantine_until"),
        "quarantine_reason": candidate.get("quarantine_reason"),
        "quality": candidate.get("quality") if isinstance(candidate.get("quality"), dict) else {},
        "fallback_score": candidate.get("fallback_score"),
        "fallback_score_reasons": candidate.get("fallback_score_reasons") or [],
    }


def status_snapshot():
    with STATUS_LOCK:
        status = json.loads(json.dumps(STATUS))
    status["logs"] = list(LOG_BUFFER)
    return status


def status_candidate_fields(candidates, standby_max_age=600):
    from .vless import (
        apply_fallback_scores,
        assign_candidate_tags,
        candidate_is_quarantined,
    )

    assign_candidate_tags(candidates)
    apply_fallback_scores(candidates)
    tested_live = [
        public_candidate(candidate)
        for candidate in candidates
        if candidate.get("last_ok_at")
        and (
            not candidate.get("last_fail_at")
            or candidate.get("last_ok_at") >= candidate.get("last_fail_at")
        )
        and not candidate_is_quarantined(candidate)
    ]
    tested_live.sort(
        key=lambda candidate: (
            candidate.get("fallback_score") or 0,
            candidate.get("last_ok_at") or 0,
            -(candidate.get("last_latency") or 999999),
        ),
        reverse=True,
    )
    return {
        "candidates_count": len(candidates),
        "sources": dict(Counter(candidate.get("source") for candidate in candidates)),
        "quarantine_count": sum(1 for candidate in candidates if candidate_is_quarantined(candidate)),
        "tested_live_candidates": tested_live,
        "candidates": [public_candidate(candidate) for candidate in candidates],
    }


def status_config_fields(config):
    routing = config.get("routing", {})
    direct_domains = []
    direct_ips = []
    for rule in routing.get("rules", []):
        if rule.get("outboundTag") != "direct":
            continue
        direct_domains.extend(rule.get("domain", []))
        direct_ips.extend(rule.get("ip", []))
    return {
        "routing": {
            "direct_domains": direct_domains,
            "direct_ips": direct_ips,
            "balancers": [balancer.get("tag") for balancer in routing.get("balancers", [])],
        }
    }
