import signal
import time

from .assets import prepare_assets, refresh_assets, set_asset_status
from .candidate_checker import candidate_check_enabled, random_check_delay, run_random_candidate_check
from .health import build_health_checks
from .state import load_state, persist_state
from .status import log, public_candidate, set_status, status_candidate_fields, status_snapshot
from .subscription import load_candidates
from .tcp_switch import set_switch_targets, start_switches, stop_switches
from .telegram import native_recovery_message, send_telegram_notification
from .util import load_json_file
from .vless import (
    candidate_is_quarantined,
    clear_expired_quarantine,
    native_candidate_order,
    primary_candidate,
    promote_candidate,
    quarantine_candidate,
    standby_candidate,
)
from .xray_process import curl_check, start_native_xray, terminate_process, throughput_check


SLOTS = (
    {
        "name": "active-a",
        "config_path": "/tmp/proxy-xray-active-a.json",
        "socks_port": 11080,
        "http_port": 18123,
        "dns_port": 15353,
        "api_port": 11085,
        "vless_port": 11086,
    },
    {
        "name": "active-b",
        "config_path": "/tmp/proxy-xray-active-b.json",
        "socks_port": 12080,
        "http_port": 18223,
        "dns_port": 15354,
        "api_port": 12085,
        "vless_port": 12086,
    },
)


def fresh_slot(template):
    slot = dict(template)
    slot.update(
        {
            "proc": None,
            "candidate": None,
            "candidate_uri": None,
            "fingerprint": None,
            "healthy": False,
            "failures": 0,
            "last_health": None,
            "started_at": None,
        }
    )
    return slot


def slot_alive(slot):
    proc = slot.get("proc")
    return bool(proc and proc.poll() is None)


def stop_slot(slot):
    terminate_process(slot.get("proc"))
    slot["proc"] = None
    slot["healthy"] = False


def candidate_by_uri(candidates, uri):
    if not uri:
        return None
    for candidate in candidates:
        if candidate.get("uri") == uri:
            return candidate
    return None


def refresh_slot_candidate_refs(slots, candidates):
    for slot in slots:
        candidate = candidate_by_uri(candidates, slot.get("candidate_uri"))
        if candidate:
            slot["candidate"] = candidate


def select_standby_candidate(candidates, active_candidate, args):
    clear_expired_quarantine(candidates)
    standby = standby_candidate(candidates, primary=active_candidate, max_age=args.standby_max_age)
    if standby:
        return standby, "fresh tested standby"
    standby = standby_candidate(candidates, primary=active_candidate, max_age=0)
    if standby:
        return standby, "last-known-good standby"
    active_uri = active_candidate.get("uri") if active_candidate else None
    for candidate in native_candidate_order(candidates):
        if candidate.get("uri") == active_uri:
            continue
        if candidate_is_quarantined(candidate):
            continue
        return candidate, "cold standby"
    return None, "none"


def candidate_label(candidate):
    return candidate.get("tag") or candidate.get("name") or f"{candidate.get('host')}:{candidate.get('port')}"


def start_slot(slot, candidate, args, rules, inject, active=False):
    stop_slot(slot)
    proc, _ordered, fingerprint = start_native_xray(
        [candidate],
        args,
        rules,
        inject,
        config_path=slot["config_path"],
        socks_port=slot["socks_port"],
        http_port=slot["http_port"],
        dns_port=slot["dns_port"],
        api_port=slot["api_port"],
        inbound_vless_port=slot["vless_port"],
        label=f"{slot['name']} {'active' if active else 'hot standby'}",
        update_status_config=active,
    )
    slot["proc"] = proc
    slot["candidate"] = candidate
    slot["candidate_uri"] = candidate.get("uri")
    slot["fingerprint"] = fingerprint
    slot["healthy"] = False
    slot["failures"] = 0
    slot["last_health"] = None
    slot["started_at"] = time.time()
    return slot


def check_slot(slot, args, record_candidate=True):
    if not slot.get("candidate") or not slot_alive(slot):
        slot["healthy"] = False
        slot["failures"] += 1
        slot["last_health"] = {"time": time.time(), "status": "failed", "latency": None}
        return False, None

    ok, latency = curl_check(slot["socks_port"], args.health_url, args.health_timeout)
    now = time.time()
    slot["healthy"] = ok
    slot["last_health"] = {
        "time": now,
        "status": "ok" if ok else "failed",
        "latency": round(latency, 3) if latency is not None else None,
    }
    if ok:
        slot["failures"] = 0
        if record_candidate:
            slot["candidate"]["last_ok_at"] = now
            slot["candidate"]["last_latency"] = latency
    else:
        slot["failures"] += 1
        if record_candidate:
            slot["candidate"]["last_fail_at"] = now
    return ok, latency


def check_slot_large_download(slot, args):
    candidate = slot.get("candidate")
    if not candidate or not slot_alive(slot):
        return False, 0.0
    ok, throughput_kbps = throughput_check(slot["socks_port"], args.throughput_url, args.throughput_max_time)
    now = time.time()
    if ok and throughput_kbps >= args.throughput_min_kbps:
        candidate["last_ok_at"] = now
        candidate["last_throughput_kbps"] = round(throughput_kbps)
        log(
            f"hot standby large download ok {candidate_label(candidate)}: "
            f"{throughput_kbps:.0f} kbps"
        )
        return True, throughput_kbps
    candidate["last_fail_at"] = now
    log(
        f"hot standby large download failed {candidate_label(candidate)}: "
        f"{throughput_kbps:.0f} kbps < {args.throughput_min_kbps:.0f} kbps"
    )
    return False, throughput_kbps


def slot_public_status(slot):
    candidate = slot.get("candidate")
    return {
        "name": slot.get("name"),
        "running": slot_alive(slot),
        "healthy": slot.get("healthy"),
        "failures": slot.get("failures", 0),
        "ports": {
            "socks": slot.get("socks_port"),
            "http": slot.get("http_port"),
            "vless": slot.get("vless_port"),
            "api": slot.get("api_port"),
        },
        "candidate": public_candidate(candidate) if candidate else None,
        "last_health": slot.get("last_health"),
        "started_at": slot.get("started_at"),
    }


def active_path_for_slot(slot):
    candidate = slot.get("candidate")
    selected = public_candidate(candidate) if candidate else None
    return {
        "status": "ok" if slot_alive(slot) else "fail",
        "detail": "dual-active slot via TCP switch",
        "time": time.time(),
        "balancer": "hot-standby",
        "strategy": "active-slot",
        "selected_tag": selected.get("tag") if selected else None,
        "selected": selected,
        "fallback": selected,
        "raw": "",
    }


def set_runtime_status(candidates, args, active_slot, standby_slot):
    set_status(
        **status_candidate_fields(candidates, args.standby_max_age),
        xray_running=slot_alive(active_slot),
        active_backend=slot_public_status(active_slot),
        hot_standby=slot_public_status(standby_slot),
        active_path=active_path_for_slot(active_slot),
    )


def rebuild_standby(standby_slot, candidates, active_candidate, args, rules, inject, reason):
    candidate, mode = select_standby_candidate(candidates, active_candidate, args)
    if not candidate:
        log(f"{reason}; no candidate available for hot standby")
        stop_slot(standby_slot)
        return None
    log(
        f"{reason}; starting hot standby from {mode}: "
        f"{candidate_label(candidate)} ({candidate.get('host')}:{candidate.get('port')})"
    )
    start_slot(standby_slot, candidate, args, rules, inject, active=False)
    return candidate


def run(args):
    prepare_assets(args)
    if args.asset_refresh_on_start:
        refresh_assets(args, reason="startup")

    rules = load_json_file(args.rules_file, {"rules": []})
    inject = load_json_file(args.inject_file, {})
    state = load_state(args.state_file)
    candidates = []
    while not candidates:
        candidates = load_candidates(args, state=state, allow_proxy=False)
        if not candidates:
            log(f"no usable VLESS candidates after filtering; retrying in {args.retry_interval}s")
            time.sleep(args.retry_interval)
    startup_subscription_status = status_snapshot().get("last_subscription_status") or {}

    candidates = native_candidate_order(candidates)
    active_slot = fresh_slot(SLOTS[0])
    standby_slot = fresh_slot(SLOTS[1])
    switches = {}
    stopping = False

    primary = primary_candidate(candidates)
    start_slot(active_slot, primary, args, rules, inject, active=True)
    switches = start_switches(active_slot, args)
    rebuild_standby(standby_slot, candidates, active_slot.get("candidate"), args, rules, inject, "startup")
    persist_state(args, candidates, active=active_slot.get("candidate"))
    set_status(
        standby_policy={
            "max_age": args.standby_max_age,
            "cooldown": args.failover_cooldown,
            "quarantine_duration": args.quarantine_duration,
        },
    )
    set_runtime_status(candidates, args, active_slot, standby_slot)

    now = time.monotonic()
    next_asset_refresh = now + args.asset_refresh_interval if args.asset_refresh_interval > 0 else None
    if startup_subscription_status.get("status") == "ok" or args.sub_post_start_refresh_delay <= 0:
        next_refresh = now + args.refresh_interval
    else:
        next_refresh = now + args.sub_post_start_refresh_delay
        log(
            "initial subscription refresh did not succeed; "
            f"retrying through the running proxy in {args.sub_post_start_refresh_delay}s"
        )
    next_health_check = now + 3
    next_throughput_check = now + 8 if args.throughput_check_interval > 0 else None
    next_diagnostics_check = now + 2
    next_active_path_check = now + 2
    checks_enabled = candidate_check_enabled(args)
    next_candidate_check = now + random_check_delay(args) if checks_enabled else None
    if checks_enabled:
        set_status(
            candidate_checker={
                "enabled": True,
                "min_interval": args.candidate_check_min_interval,
                "max_interval": args.candidate_check_max_interval,
                "extra_weight": args.candidate_check_extra_weight,
            },
            next_candidate_check_at=time.time() + max(0, next_candidate_check - now),
        )
    else:
        set_status(candidate_checker={"enabled": False, "min_interval": None, "max_interval": None})

    failures = 0
    slow_checks = 0
    throughput_slow_checks = 0
    last_health_status = None
    last_throughput_status = None
    switch_cooldown_until = 0.0

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        stop_switches(switches)
        stop_slot(active_slot)
        stop_slot(standby_slot)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while not stopping:
        due_times = [next_refresh, next_health_check, next_diagnostics_check]
        if args.active_path_interval > 0:
            due_times.append(next_active_path_check)
        if next_asset_refresh is not None:
            due_times.append(next_asset_refresh)
        if next_throughput_check is not None:
            due_times.append(next_throughput_check)
        if next_candidate_check is not None:
            due_times.append(next_candidate_check)
        sleep_for = max(0.1, min(due_times) - time.monotonic())
        time.sleep(min(sleep_for, 1.0))
        now = time.monotonic()

        if now >= next_refresh:
            try:
                state = load_state(args.state_file)
                new_candidates = load_candidates(args, candidates, state=state, allow_proxy=True)
                next_refresh = time.monotonic() + args.refresh_interval
                candidates = native_candidate_order(new_candidates)
                refresh_slot_candidate_refs((active_slot, standby_slot), candidates)
                if not slot_alive(standby_slot) or not standby_slot.get("candidate"):
                    rebuild_standby(standby_slot, candidates, active_slot.get("candidate"), args, rules, inject, "subscription refresh")
                persist_state(args, candidates, active=active_slot.get("candidate"))
                set_status(last_refresh_at=time.time())
                set_runtime_status(candidates, args, active_slot, standby_slot)
            except Exception as exc:
                log(f"subscription refresh failed: {exc}")

        if next_asset_refresh is not None and now >= next_asset_refresh:
            try:
                assets_changed = refresh_assets(args, reason="scheduled")
                next_asset_refresh = time.monotonic() + args.asset_refresh_interval
                if assets_changed:
                    log("geo assets changed; rebuilding active and hot standby xray slots")
                    active_candidate = active_slot.get("candidate")
                    standby_candidate_obj = standby_slot.get("candidate")
                    if active_candidate:
                        start_slot(active_slot, active_candidate, args, rules, inject, active=True)
                        set_switch_targets(switches, active_slot)
                    if standby_candidate_obj:
                        start_slot(standby_slot, standby_candidate_obj, args, rules, inject, active=False)
                    set_runtime_status(candidates, args, active_slot, standby_slot)
                    continue
            except Exception as exc:
                log(f"asset refresh failed: {exc}")
                set_asset_status(args)
                next_asset_refresh = time.monotonic() + args.asset_refresh_interval

        if now >= next_health_check:
            next_health_check = time.monotonic() + args.check_interval
            if not slot_alive(active_slot):
                failures = args.max_failures
                log("active xray slot exited")
                set_status(xray_running=False)
            else:
                ok, latency = curl_check(1080, args.health_url, args.health_timeout)
                if ok:
                    failures = 0
                    active_slot["healthy"] = True
                    active_slot["failures"] = 0
                    last_health_status = {"time": time.time(), "status": "ok", "latency": round(latency, 3)}
                    active_slot["last_health"] = last_health_status
                    if active_slot.get("candidate"):
                        active_slot["candidate"]["last_ok_at"] = time.time()
                        active_slot["candidate"]["last_latency"] = latency
                    set_status(xray_running=True, last_health=last_health_status)
                    if args.degrade_latency > 0 and latency >= args.degrade_latency:
                        slow_checks += 1
                        log(
                            f"health degraded {slow_checks}/{args.degrade_checks}: "
                            f"{latency:.3f}s >= {args.degrade_latency:.3f}s"
                        )
                    else:
                        slow_checks = 0
                else:
                    failures += 1
                    active_slot["healthy"] = False
                    active_slot["failures"] = failures
                    slow_checks = 0
                    log(f"health failed {failures}/{args.max_failures}")
                    if active_slot.get("candidate"):
                        active_slot["candidate"]["last_fail_at"] = time.time()
                    last_health_status = {"time": time.time(), "status": "failed", "latency": None}
                    active_slot["last_health"] = last_health_status
                    set_status(xray_running=True, last_health=last_health_status)
                set_status(failures=failures, slow_checks=slow_checks)

            if standby_slot.get("candidate"):
                standby_ok, standby_latency = check_slot(standby_slot, args)
                if standby_ok:
                    log(
                        f"hot standby ok "
                        f"{standby_slot['candidate'].get('tag')} ({standby_latency:.3f}s)"
                    )
                elif standby_slot.get("failures", 0) >= args.max_failures:
                    failed = standby_slot.get("candidate")
                    if failed:
                        quarantine_candidate(failed, args.quarantine_duration, "hot standby failed")
                    rebuild_standby(
                        standby_slot,
                        candidates,
                        active_slot.get("candidate"),
                        args,
                        rules,
                        inject,
                        "hot standby failed",
                    )
                set_status(hot_standby=slot_public_status(standby_slot))

            set_runtime_status(candidates, args, active_slot, standby_slot)

        if (
            args.throughput_check_interval > 0
            and next_throughput_check is not None
            and now >= next_throughput_check
            and slot_alive(active_slot)
        ):
            ok, throughput_kbps = throughput_check(1080, args.throughput_url, args.throughput_max_time)
            next_throughput_check = time.monotonic() + args.throughput_check_interval
            if ok and throughput_kbps >= args.throughput_min_kbps:
                throughput_slow_checks = 0
                log(f"throughput ok: {throughput_kbps:.0f} kbps")
                last_throughput_status = {
                    "time": time.time(),
                    "status": "ok",
                    "kbps": round(throughput_kbps),
                }
                if active_slot.get("candidate"):
                    active_slot["candidate"]["last_throughput_kbps"] = round(throughput_kbps)
                set_status(last_throughput=last_throughput_status)
            else:
                throughput_slow_checks += 1
                log(
                    f"throughput degraded {throughput_slow_checks}/{args.throughput_degrade_checks}: "
                    f"{throughput_kbps:.0f} kbps < {args.throughput_min_kbps:.0f} kbps"
                )
                last_throughput_status = {
                    "time": time.time(),
                    "status": "degraded",
                    "kbps": round(throughput_kbps),
                }
                set_status(last_throughput=last_throughput_status)
            set_status(throughput_slow_checks=throughput_slow_checks)

        if now >= next_diagnostics_check:
            snapshot = status_snapshot()
            health_checks = build_health_checks(
                args,
                slot_alive(active_slot),
                last_health_status or snapshot.get("last_health"),
                last_throughput_status or snapshot.get("last_throughput"),
                snapshot.get("last_subscription_status"),
            )
            set_status(health_checks=health_checks, last_health_checks_at=time.time())
            next_diagnostics_check = time.monotonic() + args.check_interval

        if args.active_path_interval > 0 and now >= next_active_path_check:
            set_status(active_path=active_path_for_slot(active_slot))
            next_active_path_check = time.monotonic() + args.active_path_interval

        failover_reason = None
        if failures >= args.max_failures:
            failover_reason = f"connection failed {failures}/{args.max_failures}"
        elif args.degrade_checks > 0 and slow_checks >= args.degrade_checks:
            failover_reason = (
                f"connection degraded {slow_checks}/{args.degrade_checks}; "
                f"latency >= {args.degrade_latency:.3f}s"
            )
        elif args.throughput_degrade_checks > 0 and throughput_slow_checks >= args.throughput_degrade_checks:
            failover_reason = (
                f"throughput degraded {throughput_slow_checks}/{args.throughput_degrade_checks}; "
                f"speed < {args.throughput_min_kbps:.0f} kbps"
            )

        if failover_reason:
            full_failure = failures >= args.max_failures
            if switch_cooldown_until and time.monotonic() < switch_cooldown_until and not full_failure:
                remaining = max(1, int(switch_cooldown_until - time.monotonic()))
                log(f"{failover_reason}; switch suppressed by cooldown for {remaining}s")
                failures = 0
                slow_checks = 0
                throughput_slow_checks = 0
                set_status(
                    failures=0,
                    slow_checks=0,
                    throughput_slow_checks=0,
                    switch_cooldown_until=time.time() + remaining,
                )
                continue

            old_active_slot = active_slot
            old_standby_slot = standby_slot
            old_active_candidate = old_active_slot.get("candidate")
            standby_ok = slot_alive(old_standby_slot) and old_standby_slot.get("healthy")
            if not standby_ok and slot_alive(old_standby_slot):
                standby_ok, _latency = check_slot(old_standby_slot, args)

            if old_active_candidate:
                quarantine_candidate(old_active_candidate, args.quarantine_duration, failover_reason)

            if not standby_ok:
                log(f"{failover_reason}; hot standby is not healthy, trying cold replacement")
                failed_standby_candidate = old_standby_slot.get("candidate")
                if failed_standby_candidate:
                    quarantine_candidate(failed_standby_candidate, args.quarantine_duration, "hot standby was unhealthy")
                rebuild_standby(
                    old_standby_slot,
                    candidates,
                    old_active_candidate,
                    args,
                    rules,
                    inject,
                    "cold replacement",
                )
                standby_ok, _latency = check_slot(old_standby_slot, args)

            throughput_failover = failover_reason.startswith("throughput degraded")
            if standby_ok and throughput_failover and args.throughput_check_interval > 0:
                for attempt in range(2):
                    large_ok, _standby_kbps = check_slot_large_download(old_standby_slot, args)
                    if large_ok:
                        break
                    failed_standby_candidate = old_standby_slot.get("candidate")
                    if failed_standby_candidate:
                        quarantine_candidate(
                            failed_standby_candidate,
                            args.quarantine_duration,
                            "hot standby failed large download",
                        )
                    rebuild_standby(
                        old_standby_slot,
                        candidates,
                        old_active_candidate,
                        args,
                        rules,
                        inject,
                        "standby large download failed",
                    )
                    standby_ok, _latency = check_slot(old_standby_slot, args)
                    if not standby_ok:
                        break
                else:
                    standby_ok = False

            if standby_ok:
                active_slot, standby_slot = old_standby_slot, old_active_slot
                set_switch_targets(switches, active_slot)
                log(
                    f"{failover_reason}; switched public ports to hot standby "
                    f"{active_slot['candidate'].get('tag')} "
                    f"({active_slot['candidate'].get('host')}:{active_slot['candidate'].get('port')})"
                )
                candidates = promote_candidate(candidates, active_slot.get("candidate"))
                stop_slot(standby_slot)
                rebuild_standby(
                    standby_slot,
                    candidates,
                    active_slot.get("candidate"),
                    args,
                    rules,
                    inject,
                    "after hot switch",
                )
                persist_state(args, candidates, active=active_slot.get("candidate"))
                switch_cooldown_until = time.monotonic() + args.failover_cooldown
                set_status(
                    **status_candidate_fields(candidates, args.standby_max_age),
                    xray_running=slot_alive(active_slot),
                    failures=0,
                    slow_checks=0,
                    throughput_slow_checks=0,
                    switch_cooldown_until=time.time() + args.failover_cooldown,
                    active_backend=slot_public_status(active_slot),
                    hot_standby=slot_public_status(standby_slot),
                    active_path=active_path_for_slot(active_slot),
                    last_switch={
                        "time": time.time(),
                        "reason": failover_reason,
                        "standby_used": True,
                        "standby_mode": "hot-active",
                        "from": old_active_candidate.get("tag") if old_active_candidate else None,
                        "to": active_slot["candidate"].get("tag") if active_slot.get("candidate") else None,
                    },
                )
                time.sleep(args.post_switch_notify_delay)
                ok, latency = curl_check(1080, args.health_url, args.health_timeout)
                throughput_kbps = None
                if ok and args.throughput_check_interval > 0:
                    throughput_ok, measured = throughput_check(1080, args.throughput_url, args.throughput_max_time)
                    if throughput_ok:
                        throughput_kbps = measured
                if ok:
                    last_health_status = {"time": time.time(), "status": "ok", "latency": round(latency, 3)}
                    active_slot["healthy"] = True
                    active_slot["failures"] = 0
                    active_slot["last_health"] = last_health_status
                    if active_slot.get("candidate"):
                        active_slot["candidate"]["last_ok_at"] = last_health_status["time"]
                        active_slot["candidate"]["last_latency"] = latency
                    set_status(last_health=last_health_status, active_backend=slot_public_status(active_slot))
                    send_telegram_notification(
                        args,
                        native_recovery_message(failover_reason, [active_slot["candidate"]], latency, throughput_kbps),
                    )
                if throughput_kbps:
                    last_throughput_status = {
                        "time": time.time(),
                        "status": "ok",
                        "kbps": round(throughput_kbps),
                    }
                    if active_slot.get("candidate"):
                        active_slot["candidate"]["last_throughput_kbps"] = round(throughput_kbps)
                    set_status(last_throughput=last_throughput_status)
                failures = 0
                slow_checks = 0
                throughput_slow_checks = 0
                continue

            log(f"{failover_reason}; no healthy standby was available")
            failures = 0
            slow_checks = 0
            throughput_slow_checks = 0
            set_status(failures=0, slow_checks=0, throughput_slow_checks=0)

        if next_candidate_check is not None and time.monotonic() >= next_candidate_check:
            run_random_candidate_check(candidates, args)
            persist_state(args, candidates, active=active_slot.get("candidate"))
            candidates = native_candidate_order(candidates)
            refresh_slot_candidate_refs((active_slot, standby_slot), candidates)
            if not slot_alive(standby_slot) or standby_slot.get("failures", 0) >= args.max_failures:
                rebuild_standby(standby_slot, candidates, active_slot.get("candidate"), args, rules, inject, "candidate check")
            delay = random_check_delay(args)
            next_candidate_check = time.monotonic() + delay
            set_status(
                **status_candidate_fields(candidates, args.standby_max_age),
                active_backend=slot_public_status(active_slot),
                hot_standby=slot_public_status(standby_slot),
                next_candidate_check_at=time.time() + delay,
            )
