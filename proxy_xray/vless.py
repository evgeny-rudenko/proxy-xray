import json
import random
import re
import time
import urllib.parse

from .util import csv, maybe_bool, qfirst


RU_MARKERS = (
    "🇷🇺",
    " ru ",
    "[ru]",
    "(ru)",
    "-ru",
    "_ru",
    " russia",
    "russian",
    "россия",
    "москва",
    "moscow",
)

US_MARKERS = (
    "🇺🇸",
    " usa",
    " u.s.",
    " united states",
    " america",
    " new york",
    " los angeles",
    " miami",
    " chicago",
    " dallas",
    " seattle",
    " ashburn",
)

EU_MARKERS = (
    "🇩🇪",
    "🇳🇱",
    "🇫🇷",
    "🇬🇧",
    "🇬🇧",
    "🇫🇮",
    "🇸🇪",
    "🇳🇴",
    "🇵🇱",
    "🇨🇿",
    "🇦🇹",
    "🇮🇹",
    "🇪🇸",
    " germany",
    " netherlands",
    " france",
    " finland",
    " sweden",
    " norway",
    " poland",
    " czech",
    " austria",
    " italy",
    " spain",
    " london",
    " amsterdam",
    " frankfurt",
    " berlin",
    " paris",
    " helsinki",
    " stockholm",
    " warsaw",
)

EU_TLDS = (
    ".de",
    ".nl",
    ".fr",
    ".fi",
    ".se",
    ".no",
    ".pl",
    ".cz",
    ".at",
    ".it",
    ".es",
    ".uk",
)

NETWORK_PRIORITY = {
    "tcp": 0,
    "grpc": 2,
    "ws": 3,
    "httpupgrade": 4,
    "xhttp": 6,
    "splithttp": 7,
}


def parse_vless_uri(uri, index):
    uri = uri.strip()
    if not uri.startswith("vless://"):
        return None

    parsed = urllib.parse.urlsplit(uri)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    uuid = urllib.parse.unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port or 443
    name = urllib.parse.unquote(parsed.fragment or f"{host}:{port}")
    if not uuid or not host:
        return None

    network = qfirst(query, "type", default="tcp") or "tcp"
    security = qfirst(query, "security", default="none") or "none"
    flow = qfirst(query, "flow")
    encryption = qfirst(query, "encryption", default="none") or "none"

    user = {"id": uuid, "encryption": encryption, "level": 0}
    if flow:
        user["flow"] = flow

    outbound = {
        "tag": "proxy",
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": int(port),
                    "users": [user],
                }
            ]
        },
        "streamSettings": {
            "network": network,
            "security": security,
        },
    }

    stream = outbound["streamSettings"]
    sni = qfirst(query, "sni", "serverName")
    fingerprint = qfirst(query, "fp", "fingerprint")
    alpn = csv(qfirst(query, "alpn"))

    if security == "reality":
        settings = {
            "publicKey": qfirst(query, "pbk", "publicKey"),
            "serverName": sni,
            "shortId": qfirst(query, "sid", "shortId"),
            "fingerprint": fingerprint,
            "spiderX": qfirst(query, "spx", "spiderX"),
        }
        stream["realitySettings"] = {k: v for k, v in settings.items() if v}
    elif security == "tls":
        settings = {
            "serverName": sni,
            "fingerprint": fingerprint,
            "alpn": alpn,
            "allowInsecure": maybe_bool(qfirst(query, "allowInsecure")),
        }
        stream["tlsSettings"] = {k: v for k, v in settings.items() if v not in ("", [], False)}

    path = qfirst(query, "path")
    host_header = qfirst(query, "host")
    service_name = qfirst(query, "serviceName", "service")

    if network == "ws":
        settings = {"path": path or "/"}
        if host_header:
            settings["headers"] = {"Host": host_header}
        stream["wsSettings"] = settings
    elif network == "grpc":
        settings = {"serviceName": service_name}
        if qfirst(query, "mode") == "multi":
            settings["multiMode"] = True
        if host_header:
            settings["authority"] = host_header
        stream["grpcSettings"] = {k: v for k, v in settings.items() if v not in ("", None)}
    elif network == "splithttp":
        settings = {"path": path or "/", "host": host_header}
        stream["splithttpSettings"] = {k: v for k, v in settings.items() if v}
    elif network == "xhttp":
        settings = {
            "path": path or "/",
            "host": host_header,
            "mode": qfirst(query, "mode"),
        }
        extra = qfirst(query, "extra")
        if extra:
            try:
                settings["extra"] = json.loads(extra)
            except json.JSONDecodeError:
                pass
        concurrency = qfirst(query, "concurrency")
        if concurrency and "extra" not in settings:
            settings["extra"] = {"xmux": {"maxConcurrency": concurrency}}
        stream["xhttpSettings"] = {k: v for k, v in settings.items() if v}
    elif network == "httpupgrade":
        settings = {"path": path or "/", "host": host_header}
        stream["httpupgradeSettings"] = {k: v for k, v in settings.items() if v}

    return {
        "index": index,
        "uri": uri,
        "name": name,
        "host": host,
        "port": int(port),
        "outbound": outbound,
        "region_score": 2,
        "last_latency": None,
        "last_ok_at": None,
        "last_fail_at": None,
        "quarantine_until": None,
        "quarantine_reason": None,
        "network_score": NETWORK_PRIORITY.get(network, 9),
        "source": "subscription",
        "source_score": 1,
    }


def parse_subscription(text):
    candidates = []
    index = 0
    for token in re.split(r"[\r\n]+", text):
        token = token.strip()
        if not token:
            continue
        item = parse_vless_uri(token, index)
        if item:
            candidates.append(item)
            index += 1
    return candidates


def parse_vless_lines(text, source, source_score, start_index=0):
    candidates = []
    index = start_index
    for token in re.split(r"[\r\n]+", text):
        token = token.strip()
        if not token or token.startswith("#"):
            continue
        item = parse_vless_uri(token, index)
        if item:
            item["source"] = source
            item["source_score"] = source_score
            candidates.append(item)
            index += 1
    return candidates


def normalized_candidate_text(candidate):
    return f" {candidate['name']} {candidate['host']} ".lower()


def is_ru_candidate(candidate, excludes):
    text = normalized_candidate_text(candidate)
    host = candidate["host"].lower()
    if host.endswith(".ru") or host.endswith(".su") or host.endswith(".xn--p1ai") or host.endswith(".рф"):
        return True
    markers = list(RU_MARKERS) + [f" {item.lower()} " for item in excludes if item]
    return any(marker in text for marker in markers)


def region_score(candidate, prefer):
    text = normalized_candidate_text(candidate)
    host = candidate["host"].lower()
    if "us" in prefer:
        if host.endswith(".us") or any(marker in text for marker in US_MARKERS):
            return 0
    if "eu" in prefer:
        if host.endswith(EU_TLDS) or any(marker in text for marker in EU_MARKERS):
            return 1
    return 2


def filter_and_rank(candidates, prefer, excludes):
    filtered = []
    for candidate in candidates:
        if candidate.get("source") != "extra" and is_ru_candidate(candidate, excludes):
            continue
        candidate["region_score"] = region_score(candidate, prefer)
        filtered.append(candidate)
    random.shuffle(filtered)
    return sorted(filtered, key=lambda c: (c["source_score"], c["region_score"], c["network_score"]))


def merge_candidate_state(old_candidates, new_candidates):
    old = {candidate["uri"]: candidate for candidate in old_candidates}
    for candidate in new_candidates:
        previous = old.get(candidate["uri"])
        if previous:
            candidate["last_latency"] = previous.get("last_latency")
            candidate["last_throughput_kbps"] = previous.get("last_throughput_kbps")
            candidate["last_ok_at"] = previous.get("last_ok_at")
            candidate["last_fail_at"] = previous.get("last_fail_at")
            candidate["quarantine_until"] = previous.get("quarantine_until")
            candidate["quarantine_reason"] = previous.get("quarantine_reason")
            candidate["quality"] = previous.get("quality") if isinstance(previous.get("quality"), dict) else {}
            candidate["recent_checks"] = previous.get("recent_checks") if isinstance(previous.get("recent_checks"), list) else []
    return new_candidates


def assign_candidate_tags(candidates):
    counters = {"extra": 0, "subscription": 0}
    for candidate in candidates:
        source = candidate.get("source", "subscription")
        if source == "extra":
            tag = f"proxy-extra-{counters['extra']}"
            counters["extra"] += 1
        else:
            tag = f"proxy-sub-{counters['subscription']}"
            counters["subscription"] += 1
        candidate["tag"] = tag
        candidate["outbound"]["tag"] = tag
    return candidates


def candidate_is_live(candidate):
    return bool(
        candidate.get("last_ok_at")
        and (
            not candidate.get("last_fail_at")
            or candidate.get("last_ok_at") >= candidate.get("last_fail_at")
        )
    )


def candidate_is_preferred_region(candidate):
    if candidate.get("source") == "extra":
        return True
    return int(candidate.get("region_score", 2)) < 2


def clamp(value, minimum=0.0, maximum=1.0):
    return max(minimum, min(maximum, value))


def candidate_fallback_score(candidate, now=None):
    now = now or time.time()
    score = 1000.0
    reasons = []

    if candidate.get("source") == "extra":
        score += 120
        reasons.append("+120 extra")
    else:
        score += 20
        reasons.append("+20 subscription")

    priority_boost = candidate.get("priority_boost")
    if priority_boost:
        score += float(priority_boost)
        reasons.append(f"+{float(priority_boost):.0f} priority")

    region_bonus = {0: 35, 1: 25, 2: 0}.get(candidate.get("region_score", 2), 0)
    if region_bonus:
        score += region_bonus
        reasons.append(f"+{region_bonus} preferred-region")
    elif candidate.get("source") != "extra":
        score -= 140
        reasons.append("-140 non-preferred-region")

    network_bonus = max(0, 9 - int(candidate.get("network_score", 9))) * 4
    if network_bonus:
        score += network_bonus
        reasons.append(f"+{network_bonus} transport")

    last_ok_at = candidate.get("last_ok_at")
    last_fail_at = candidate.get("last_fail_at")
    live = candidate_is_live(candidate)
    if last_ok_at:
        ok_age = max(0.0, now - float(last_ok_at))
        freshness = clamp(1.0 - ok_age / (7 * 24 * 3600))
        ok_bonus = 280 * freshness
        if live:
            score += ok_bonus
            reasons.append(f"+{ok_bonus:.0f} recent-ok")
        else:
            stale_bonus = 60 * freshness
            score += stale_bonus
            reasons.append(f"+{stale_bonus:.0f} old-ok")

    if last_fail_at:
        fail_age = max(0.0, now - float(last_fail_at))
        fail_freshness = clamp(1.0 - fail_age / (24 * 3600))
        fail_penalty = 360 * fail_freshness
        if fail_penalty > 0:
            score -= fail_penalty
            reasons.append(f"-{fail_penalty:.0f} recent-fail")

    latency = candidate.get("last_latency")
    if latency is not None:
        latency_penalty = min(220.0, max(0.0, float(latency) - 0.25) * 35)
        if latency_penalty > 0:
            score -= latency_penalty
            reasons.append(f"-{latency_penalty:.0f} latency")

    throughput = candidate.get("last_throughput_kbps")
    if throughput:
        throughput_bonus = min(80.0, float(throughput) / 1000.0)
        score += throughput_bonus
        reasons.append(f"+{throughput_bonus:.0f} throughput")

    quality = candidate.get("quality") if isinstance(candidate.get("quality"), dict) else {}
    success_rate = quality.get("success_rate")
    if success_rate is not None and quality.get("checks", 0) >= 3:
        rate_bonus = (float(success_rate) - 0.5) * 160
        score += rate_bonus
        reasons.append(f"{rate_bonus:+.0f} history")

    consecutive_failures = int(quality.get("consecutive_failures") or 0)
    if consecutive_failures:
        failure_penalty = min(240, consecutive_failures * 80)
        score -= failure_penalty
        reasons.append(f"-{failure_penalty} fail-streak")

    latency_ewma = quality.get("latency_ewma")
    if latency_ewma is not None:
        latency_history_penalty = min(120.0, max(0.0, float(latency_ewma) - 0.35) * 25)
        if latency_history_penalty > 0:
            score -= latency_history_penalty
            reasons.append(f"-{latency_history_penalty:.0f} latency-history")

    throughput_ewma = quality.get("throughput_ewma")
    if throughput_ewma:
        throughput_history_bonus = min(60.0, float(throughput_ewma) / 1500.0)
        score += throughput_history_bonus
        reasons.append(f"+{throughput_history_bonus:.0f} speed-history")

    if not last_ok_at and not last_fail_at:
        reasons.append("unchecked")

    quarantine_until = candidate.get("quarantine_until")
    if quarantine_until and float(quarantine_until) > now:
        score -= 900
        reasons.append("-900 quarantine")

    return round(score, 3), reasons


def apply_fallback_scores(candidates, now=None):
    now = now or time.time()
    for candidate in candidates:
        score, reasons = candidate_fallback_score(candidate, now)
        candidate["fallback_score"] = score
        candidate["fallback_score_reasons"] = reasons
    return candidates


def native_candidate_order(candidates):
    apply_fallback_scores(candidates)
    return sorted(
        candidates,
        key=lambda candidate: (
            -(candidate.get("fallback_score") or 0),
            candidate.get("region_score", 2),
            candidate.get("network_score", 9),
            -(candidate.get("last_ok_at") or 0),
            candidate.get("index", 999999),
            candidate.get("host", ""),
            candidate.get("port", 0),
            candidate.get("uri", ""),
        ),
    )


def quarantine_candidate(candidate, duration, reason, now=None):
    if not candidate or duration <= 0:
        return
    now = now or time.time()
    candidate["quarantine_until"] = now + duration
    candidate["quarantine_reason"] = reason
    candidate["last_fail_at"] = now


def clear_expired_quarantine(candidates, now=None):
    now = now or time.time()
    for candidate in candidates:
        quarantine_until = candidate.get("quarantine_until")
        if quarantine_until and float(quarantine_until) <= now:
            candidate["quarantine_until"] = None
            candidate["quarantine_reason"] = None


def candidate_is_quarantined(candidate, now=None):
    now = now or time.time()
    quarantine_until = candidate.get("quarantine_until")
    return bool(quarantine_until and float(quarantine_until) > now)


def primary_candidate(candidates):
    ordered = native_candidate_order(candidates)
    for preferred_only in (True, False):
        for candidate in ordered:
            if preferred_only and not candidate_is_preferred_region(candidate):
                continue
            if candidate_is_quarantined(candidate):
                continue
            return candidate
    return None


def standby_candidate(candidates, primary=None, max_age=600, now=None):
    now = now or time.time()
    primary_uri = primary.get("uri") if primary else None
    ordered = native_candidate_order(candidates)
    for preferred_only in (True, False):
        for candidate in ordered:
            if preferred_only and not candidate_is_preferred_region(candidate):
                continue
            if primary_uri and candidate.get("uri") == primary_uri:
                continue
            if candidate_is_quarantined(candidate, now):
                continue
            if not candidate_is_live(candidate):
                continue
            last_ok_at = candidate.get("last_ok_at")
            if max_age > 0 and last_ok_at and now - float(last_ok_at) > max_age:
                continue
            return candidate
    return None


def promote_candidate(candidates, candidate):
    if not candidate:
        return native_candidate_order(candidates)
    uri = candidate.get("uri")
    for item in candidates:
        item["priority_boost"] = 2000 if item.get("uri") == uri else 0
    ordered = native_candidate_order(candidates)
    return sorted(
        ordered,
        key=lambda item: (
            0 if item.get("uri") == uri else 1,
            -(item.get("fallback_score") or 0),
            item.get("region_score", 2),
            item.get("network_score", 9),
            item.get("index", 999999),
        ),
    )
