import re
import time

from .candidate_checker import check_extra_candidate, free_local_port
from .slot_manager import check_slot, fresh_slot, start_slot, stop_slot
from .status import log
from .tcp_switch import set_switch_targets
from .vless import quarantine_candidate
from .xray_process import throughput_check


def transport_priority(candidate):
    stream = candidate.get("outbound", {}).get("streamSettings", {})
    network = (stream.get("network") or "").lower()
    security = (stream.get("security") or "").lower()
    if network == "tcp" and security == "reality":
        return 3
    if network == "tcp" and security == "tls":
        return 4
    if network == "tcp":
        return 5
    return {
        "grpc": 0,
        "xhttp": 1,
        "splithttp": 2,
        "ws": 6,
        "httpupgrade": 7,
    }.get(network, 9)


def manual_extra_order(candidates):
    def key(candidate):
        quality = candidate.get("quality") if isinstance(candidate.get("quality"), dict) else {}
        checks = int(quality.get("checks") or 0)
        success_rate = float(quality.get("success_rate") if checks >= 3 else 0.5)
        consecutive_failures = int(quality.get("consecutive_failures") or 0)
        recent_failures = int(quality.get("last_24h_failures") or 0)
        latency = quality.get("latency_ewma")
        if latency is None:
            latency = candidate.get("last_latency")
        throughput = quality.get("throughput_ewma") or candidate.get("last_throughput_kbps") or 0
        return (
            consecutive_failures,
            transport_priority(candidate),
            recent_failures,
            -success_rate,
            float(latency) if latency is not None else 999999.0,
            -float(throughput),
            candidate.get("index", 999999),
        )

    return sorted((candidate for candidate in candidates if candidate.get("source") == "extra"), key=key)


def remaining_extra_candidates(candidates, failed_candidate):
    failed_uri = (failed_candidate or {}).get("uri")
    return manual_extra_order(
        candidate
        for candidate in candidates
        if candidate.get("source") == "extra" and candidate.get("uri") != failed_uri
    )


def verify_extra_candidates(candidates, args, reason, cancelled=lambda: False):
    verified, removed = [], []
    for candidate in manual_extra_order(candidates):
        if cancelled():
            break
        log(f"{reason}; pinned preflight {candidate.get('tag')} ({candidate.get('name')})")
        try:
            result = check_extra_candidate(candidate, args, cancelled=cancelled)
        except Exception as exc:
            # A local probe error is not evidence against the remote transport.
            log(f"{reason}; could not test {candidate.get('tag')}: {type(exc).__name__}")
            continue
        if result.reason == "cancelled":
            break
        if result.ok:
            candidate["last_ok_at"] = time.time()
            candidate["last_latency"] = result.latency
            candidate["last_throughput_kbps"] = round(result.kbps)
            candidate.pop("quarantine_until", None)
            candidate.pop("quarantine_reason", None)
            verified.append(candidate)
            log(f"{reason}; extra verified {candidate.get('tag')}: {result.latency:.3f}s, {result.kbps:.0f} kbps")
        else:
            candidate["last_fail_at"] = time.time()
            quarantine_candidate(candidate, args.quarantine_duration, f"manual extra pinned preflight: {result.reason}")
            removed.append(candidate)
            log(f"{reason}; extra rejected {candidate.get('tag')}: {result.reason}")
    # Fresh pinned evidence takes precedence over older, possibly misattributed pool failures.
    return sorted(verified, key=transport_priority), removed


def fresh_extra_staging_slot():
    ports = set()
    while len(ports) < 5:
        ports.add(free_local_port())
    socks, http, dns, api, vless = sorted(ports)
    return fresh_slot({
        "name": "manual-extra",
        "config_path": f"/tmp/proxy-xray-manual-extra-{socks}.json",
        "temporary_config": True,
        "inbound_listen": "127.0.0.1",
        "socks_port": socks,
        "http_port": http,
        "dns_port": dns,
        "api_port": api,
        "vless_port": vless,
    })


def extra_warmup_seconds(args, pool_size):
    interval = str(args.observatory_probe_interval)
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", interval)
    seconds = float(match[1]) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[match[2]] if match else 10.0
    # Native sequential observatory sleeps between each outbound, not each full cycle.
    return max(2.0, args.candidate_check_start_delay, (pool_size - 1) * (seconds + 5.0))


def activate_extra_slot(active_slot, staged_slot, switches):
    set_switch_targets(switches, staged_slot)
    stop_slot(active_slot)
    return staged_slot


def stage_verified_extra_pool(slot, candidates, args, rules, inject, cancelled=lambda: False):
    if not candidates or cancelled():
        return [], None, 0.0
    # If balancing still fails, try the preferred, independently verified transport alone.
    pools = [candidates] + ([candidates[:1]] if len(candidates) > 1 else [])
    for pool in pools:
        try:
            start_slot(slot, pool, args, rules, inject, active=False, preserve_order=True)
            deadline = time.monotonic() + extra_warmup_seconds(args, len(pool))
            while time.monotonic() < deadline:
                if cancelled():
                    stop_slot(slot)
                    return [], None, 0.0
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
            successes = 0
            for attempt in range(4):
                if cancelled():
                    break
                ok, latency = check_slot(slot, args, record_candidate=False)
                successes = successes + 1 if ok else 0
                if successes >= 2:
                    quality_ok, kbps = throughput_check(slot["socks_port"], args.quality_url, args.quality_max_time)
                    if quality_ok and kbps >= args.quality_min_kbps:
                        return pool, latency, kbps
                    successes = 0
                if attempt < 3:
                    time.sleep(1.0)
            log(f"manual extra staged pool ({len(pool)}) did not stabilize; keeping existing active and standby")
        except Exception as exc:
            log(f"manual extra staging failed: {type(exc).__name__}; keeping existing active and standby")
        stop_slot(slot)
    return [], None, 0.0
