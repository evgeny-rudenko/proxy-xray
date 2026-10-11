from collections import Counter, deque
import json
import threading
import time
import uuid


LOG_BUFFER = deque(maxlen=500)
CONTROL_COMMANDS = deque()
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
    "started_at": time.time(), "mode": "amsterdam-chain", "route_mode": "unavailable",
    "route_policy": "auto", "route_policy_command": None,
    "xray_running": False, "candidates_count": 0, "sources": {}, "exit_pool": [], "relay_pool": [],
    "exit": None, "relay": None, "route_checks": {}, "route_transition": {},
    "active_path": None, "last_refresh_at": None, "last_subscription_success_at": None,
    "last_health": None, "last_quality": None, "last_throughput": None,
    "health_checks": DEFAULT_HEALTH_CHECKS, "last_health_checks_at": None,
    "last_subscription_status": None, "subscription_fetch": {}, "subscription_usage": None,
    "assets": None, "last_candidate_check": None, "next_candidate_check_at": None,
    "last_switch": None, "last_event": None, "failures": 0, "quarantine_count": 0,
    "candidates": [], "tested_live_candidates": [], "routing": {},
    "dns_transport": {"enabled": True, "global_route": "socks", "direct_fallback": False},
}


def log(message):
    line = message if message.startswith("[chain]") else f"[gateway] {message}"
    LOG_BUFFER.append({"time": time.time(), "line": line})
    with STATUS_LOCK:
        STATUS["last_event"] = {"time": time.time(), "message": message}
    print(line, flush=True)


def set_status(**kwargs):
    with STATUS_LOCK:
        STATUS.update(kwargs)


def enqueue_control_command(name, payload=None):
    command = {
        "id": uuid.uuid4().hex,
        "name": name,
        "payload": payload or {},
        "queued_at": time.time(),
    }
    with STATUS_LOCK:
        if name == "recheck_route":
            pending = STATUS.get("last_control_command") or {}
            if pending.get("name") == name and pending.get("status") in ("queued", "running"):
                return pending
        if name == "route_policy":
            pending = STATUS.get("route_policy_command") or {}
            if pending.get("status") in ("queued", "running") and pending.get("payload") == command["payload"]:
                return pending
            retained = [item for item in CONTROL_COMMANDS if item["name"] != name]
            CONTROL_COMMANDS.clear()
            CONTROL_COMMANDS.extend(retained)
            STATUS["route_policy_command"] = {**command, "status": "queued"}
        CONTROL_COMMANDS.append(command)
        STATUS["last_control_command"] = {
            **command,
            "status": "queued",
        }
    log(f"control command queued: {name}")
    return command


def pop_control_commands():
    with STATUS_LOCK:
        commands = list(CONTROL_COMMANDS)
        CONTROL_COMMANDS.clear()
    return commands


def mark_control_command(command, status, detail=None):
    with STATUS_LOCK:
        record = {
            **command,
            "status": status,
            "detail": detail,
            "finished_at": time.time(),
        }
        STATUS["last_control_command"] = record
        if command["name"] == "route_policy":
            pending = STATUS.get("route_policy_command") or {}
            if pending.get("id") == command["id"]:
                STATUS["route_policy_command"] = record


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
