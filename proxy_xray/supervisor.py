import signal
import time

from .assets import prepare_assets, refresh_assets, set_asset_status
from .candidate_checker import candidate_check_enabled, random_check_delay, run_random_candidate_check
from .failover import evaluate_failover, failover_state
from .failover_executor import execute_failover
from .health import build_health_checks
from .pool import select_active_pool
from .scheduler import due_times, initial_schedule, loop_sleep_seconds
from .state import load_state, persist_state
from .slot_execution import rebuild_standby, start_active_with_preflight
from .slot_manager import (
    SLOTS,
    check_slot,
    fresh_slot,
    record_xray_selected_candidate,
    refresh_slot_candidate_refs,
    slot_alive,
    slot_candidate_tags,
    slot_candidates,
    slot_public_status,
    start_slot,
    stop_slot,
    xray_api_status_for_slot,
)
from .status import log, mark_control_command, pop_control_commands, set_status, status_candidate_fields, status_snapshot
from .status_publisher import set_runtime_status
from .subscription import load_candidates
from .tcp_switch import set_switch_targets, start_switches, stop_switches
from .util import load_json_file
from .vless import (
    clear_expired_quarantine,
    native_candidate_order,
    quarantine_candidate,
)
from .xray_process import curl_check, throughput_check


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

    if not start_active_with_preflight(active_slot, candidates, args, rules, inject, "startup"):
        log("startup; active preflight did not find a healthy pool, exposing best available pool")
        start_slot(
            active_slot,
            select_active_pool(
                candidates,
                size=args.active_pool_size,
                extra_reserve_per_slot=getattr(args, "pool_extra_reserve_per_slot", 0),
                extra_require_live=getattr(args, "pool_extra_require_live", True),
                extra_max_age=0,
                extra_max_per_host=getattr(args, "pool_extra_max_per_host", 1),
            ),
            args,
            rules,
            inject,
            active=True,
        )
    switches = start_switches(active_slot, args)
    rebuild_standby(standby_slot, candidates, slot_candidates(active_slot), args, rules, inject, "startup")
    persist_state(args, candidates, active=active_slot.get("candidate"))
    set_status(
        standby_policy={
            "max_age": args.standby_max_age,
            "cooldown": args.failover_cooldown,
            "quarantine_duration": args.quarantine_duration,
        },
    )
    set_runtime_status(candidates, args, active_slot, standby_slot)

    checks_enabled = candidate_check_enabled(args)
    schedule = initial_schedule(args, startup_subscription_status, checks_enabled=checks_enabled)
    now = schedule["now"]
    next_asset_refresh = schedule["next_asset_refresh"]
    next_refresh = schedule["next_refresh"]
    next_health_check = schedule["next_health_check"]
    next_quality_check = schedule["next_quality_check"]
    next_throughput_check = schedule["next_throughput_check"]
    next_diagnostics_check = schedule["next_diagnostics_check"]
    next_active_path_check = schedule["next_active_path_check"]
    if schedule["subscription_retry_delay"] is not None:
        log(
            "initial subscription refresh did not succeed; "
            f"retrying through the running proxy in {schedule['subscription_retry_delay']}s"
        )
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
    quality_slow_checks = 0
    throughput_slow_checks = 0
    last_health_status = None
    last_quality_status = None
    last_throughput_status = None
    switch_cooldown_until = 0.0

    def manual_extra_order(extra_candidates):
        def key(candidate):
            quality = candidate.get("quality") if isinstance(candidate.get("quality"), dict) else {}
            checks = int(quality.get("checks") or 0)
            success_rate = float(quality.get("success_rate") if checks >= 3 else 0.5)
            consecutive_failures = int(quality.get("consecutive_failures") or 0)
            latency = quality.get("latency_ewma")
            if latency is None:
                latency = candidate.get("last_latency")
            throughput = quality.get("throughput_ewma") or candidate.get("last_throughput_kbps") or 0
            network = (candidate.get("outbound", {}).get("streamSettings", {}).get("network") or "").lower()
            network_order = {"grpc": 0, "tcp": 1, "xhttp": 2, "splithttp": 3, "ws": 4, "httpupgrade": 5}
            return (
                consecutive_failures,
                float(latency) if latency is not None else 999999.0,
                -float(throughput),
                -success_rate,
                network_order.get(network, 9),
                candidate.get("index", 999999),
            )

        return sorted(extra_candidates, key=key)

    def force_extra_active_pool(command):
        nonlocal active_slot, standby_slot, failures, slow_checks, quality_slow_checks, throughput_slow_checks
        nonlocal last_health_status, last_quality_status, switch_cooldown_until
        extra_candidates = manual_extra_order(
            [
                candidate
                for candidate in candidates
                if candidate.get("source") == "extra"
            ]
        )
        if not extra_candidates:
            log("force extra pool requested; no extra candidates are configured")
            mark_control_command(command, "failed", "no extra candidates configured")
            return
        log(f"force extra pool requested; staging {len(extra_candidates)} extra candidates in hot standby")
        start_slot(standby_slot, extra_candidates, args, rules, inject, active=False)
        time.sleep(max(2.0, float(getattr(args, "candidate_check_start_delay", 0.0))))
        ok = False
        latency = None
        for attempt in range(2):
            ok, latency = check_slot(standby_slot, args)
            if ok:
                break
            if attempt == 0:
                time.sleep(2.0)
        if not ok:
            log("force extra pool rejected; staged extra pool failed health preflight")
            rebuild_standby(standby_slot, candidates, slot_candidates(active_slot), args, rules, inject, "after failed force extra pool")
            set_runtime_status(candidates, args, active_slot, standby_slot)
            mark_control_command(command, "failed", "staged extra pool failed health preflight")
            return

        quality_ok, quality_kbps = throughput_check(standby_slot["socks_port"], args.quality_url, args.quality_max_time)
        if not quality_ok or quality_kbps < args.quality_min_kbps:
            log(
                "force extra pool rejected; staged extra pool failed quality preflight: "
                f"{quality_kbps:.0f} kbps < {args.quality_min_kbps:.0f} kbps"
            )
            rebuild_standby(standby_slot, candidates, slot_candidates(active_slot), args, rules, inject, "after failed force extra pool")
            set_runtime_status(candidates, args, active_slot, standby_slot)
            mark_control_command(command, "failed", "staged extra pool failed quality preflight")
            return

        old_active_tag = active_slot["candidate"].get("tag") if active_slot.get("candidate") else None
        active_slot, standby_slot = standby_slot, active_slot
        set_switch_targets(switches, active_slot)
        stop_slot(standby_slot)
        rebuild_standby(standby_slot, candidates, slot_candidates(active_slot), args, rules, inject, "after force extra pool")
        switch_cooldown_until = time.monotonic() + args.failover_cooldown
        failures = 0
        slow_checks = 0
        quality_slow_checks = 0
        throughput_slow_checks = 0
        last_health_status = {
            "time": time.time(),
            "status": "ok",
            "latency": round(latency, 3) if latency is not None else None,
        }
        last_quality_status = {
            "time": time.time(),
            "status": "ok",
            "kbps": round(quality_kbps),
        }
        set_status(
            last_health=last_health_status,
            last_quality=last_quality_status,
            failures=failures,
            slow_checks=slow_checks,
            quality_slow_checks=quality_slow_checks,
            throughput_slow_checks=throughput_slow_checks,
            switch_cooldown_until=time.time() + args.failover_cooldown,
            last_switch={
                "time": time.time(),
                "reason": "manual force extra pool",
                "kind": "manual_extra_pool",
                "standby_used": True,
                "standby_mode": "staged-extra",
                "from": old_active_tag,
                "to": active_slot["candidate"].get("tag") if active_slot.get("candidate") else None,
            },
            failover_state=failover_state(
                state="cooldown",
                cooldown_until=switch_cooldown_until,
                now_monotonic=time.monotonic(),
                failures=0,
                slow_checks=0,
                quality_slow_checks=0,
                throughput_slow_checks=0,
            ),
        )
        persist_state(args, candidates, active=active_slot.get("candidate"))
        set_runtime_status(candidates, args, active_slot, standby_slot)
        detail = (
            f"staged extra pool passed health in {latency:.3f}s and quality "
            f"{quality_kbps:.0f} kbps; active pool switched to {len(extra_candidates)} extra candidates"
        )
        mark_control_command(command, "done", detail)

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
        stop_switches(switches)
        stop_slot(active_slot)
        stop_slot(standby_slot)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while not stopping:
        next_due_times = due_times(
            args,
            next_refresh=next_refresh,
            next_health_check=next_health_check,
            next_diagnostics_check=next_diagnostics_check,
            next_active_path_check=next_active_path_check,
            next_asset_refresh=next_asset_refresh,
            next_quality_check=next_quality_check,
            next_throughput_check=next_throughput_check,
            next_candidate_check=next_candidate_check,
        )
        sleep_for = loop_sleep_seconds(next_due_times)
        time.sleep(min(sleep_for, 1.0))
        now = time.monotonic()

        for command in pop_control_commands():
            if command.get("name") == "force_extra_pool":
                force_extra_active_pool(command)
            else:
                log(f"unknown control command ignored: {command.get('name')}")
                mark_control_command(command, "ignored", "unknown command")

        if now >= next_refresh:
            try:
                state = load_state(args.state_file)
                new_candidates = load_candidates(args, candidates, state=state, allow_proxy=True)
                next_refresh = time.monotonic() + args.refresh_interval
                candidates = native_candidate_order(new_candidates)
                refresh_slot_candidate_refs((active_slot, standby_slot), candidates)
                if not slot_alive(standby_slot) or not slot_candidates(standby_slot):
                    rebuild_standby(standby_slot, candidates, slot_candidates(active_slot), args, rules, inject, "subscription refresh")
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
                    active_candidates = slot_candidates(active_slot)
                    standby_candidates = slot_candidates(standby_slot)
                    if active_candidates:
                        start_slot(active_slot, active_candidates, args, rules, inject, active=True)
                        set_switch_targets(switches, active_slot)
                    if standby_candidates:
                        start_slot(standby_slot, standby_candidates, args, rules, inject, active=False)
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
                    selected = record_xray_selected_candidate(active_slot, args, latency=latency, mark_ok=True)
                    if active_slot.get("candidate") and (not selected or selected is active_slot.get("candidate")):
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

            if slot_candidates(standby_slot):
                standby_ok, standby_latency = check_slot(standby_slot, args)
                if standby_ok:
                    log(
                        f"hot standby ok "
                        f"{slot_candidate_tags(standby_slot)} ({standby_latency:.3f}s)"
                    )
                elif standby_slot.get("failures", 0) >= args.max_failures:
                    failed = standby_slot.get("candidate")
                    if failed:
                        quarantine_candidate(failed, args.quarantine_duration, "hot standby failed")
                    rebuild_standby(
                        standby_slot,
                        candidates,
                        slot_candidates(active_slot),
                        args,
                        rules,
                        inject,
                        "hot standby failed",
                    )
                set_status(hot_standby=slot_public_status(standby_slot))

            set_runtime_status(candidates, args, active_slot, standby_slot)
            persist_state(args, candidates, active=active_slot.get("candidate"))

        if (
            args.quality_check_interval > 0
            and next_quality_check is not None
            and now >= next_quality_check
            and slot_alive(active_slot)
        ):
            ok, quality_kbps = throughput_check(1080, args.quality_url, args.quality_max_time)
            next_quality_check = time.monotonic() + args.quality_check_interval
            if ok and quality_kbps >= args.quality_min_kbps:
                quality_slow_checks = 0
                log(f"quality download ok: {quality_kbps:.0f} kbps")
                last_quality_status = {
                    "time": time.time(),
                    "status": "ok",
                    "kbps": round(quality_kbps),
                }
                selected = record_xray_selected_candidate(active_slot, args, throughput_kbps=quality_kbps)
                if active_slot.get("candidate") and (not selected or selected is active_slot.get("candidate")):
                    active_slot["candidate"]["last_throughput_kbps"] = round(quality_kbps)
                set_status(last_quality=last_quality_status)
            else:
                quality_slow_checks += 1
                log(
                    f"quality download degraded {quality_slow_checks}/{args.quality_degrade_checks}: "
                    f"{quality_kbps:.0f} kbps < {args.quality_min_kbps:.0f} kbps"
                )
                last_quality_status = {
                    "time": time.time(),
                    "status": "degraded",
                    "kbps": round(quality_kbps),
                }
                set_status(last_quality=last_quality_status)
            set_status(quality_slow_checks=quality_slow_checks)
            persist_state(args, candidates, active=active_slot.get("candidate"))

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
                selected = record_xray_selected_candidate(active_slot, args, throughput_kbps=throughput_kbps)
                if active_slot.get("candidate") and (not selected or selected is active_slot.get("candidate")):
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
                last_quality_status or snapshot.get("last_quality"),
                last_throughput_status or snapshot.get("last_throughput"),
                snapshot.get("last_subscription_status"),
            )
            set_status(health_checks=health_checks, last_health_checks_at=time.time())
            next_diagnostics_check = time.monotonic() + args.check_interval

        if args.active_path_interval > 0 and now >= next_active_path_check:
            active_api = xray_api_status_for_slot(active_slot, args)
            standby_api = xray_api_status_for_slot(standby_slot, args)
            set_status(
                active_path=active_api,
                active_observatory=active_api,
                standby_observatory=standby_api,
            )
            next_active_path_check = time.monotonic() + args.active_path_interval

        standby_ready_for_fast_failover = slot_alive(standby_slot) and standby_slot.get("healthy")
        decision = evaluate_failover(
            args,
            failures=failures,
            slow_checks=slow_checks,
            quality_slow_checks=quality_slow_checks,
            throughput_slow_checks=throughput_slow_checks,
            standby_ready=standby_ready_for_fast_failover,
        )
        next_failover_state = "triggered" if decision.triggered else "idle"
        if not decision.triggered and switch_cooldown_until and now < switch_cooldown_until:
            next_failover_state = "cooldown"
        set_status(
            failover_state=failover_state(
                decision,
                state=next_failover_state,
                cooldown_until=switch_cooldown_until,
                now_monotonic=now,
                failures=failures,
                slow_checks=slow_checks,
                quality_slow_checks=quality_slow_checks,
                throughput_slow_checks=throughput_slow_checks,
            )
        )

        if decision.triggered:
            result = execute_failover(
                args=args,
                decision=decision,
                now=now,
                switch_cooldown_until=switch_cooldown_until,
                failures=failures,
                slow_checks=slow_checks,
                quality_slow_checks=quality_slow_checks,
                throughput_slow_checks=throughput_slow_checks,
                active_slot=active_slot,
                standby_slot=standby_slot,
                switches=switches,
                candidates=candidates,
                rules=rules,
                inject=inject,
                last_health_status=last_health_status,
                last_throughput_status=last_throughput_status,
            )
            active_slot = result.active_slot
            standby_slot = result.standby_slot
            candidates = result.candidates
            switch_cooldown_until = result.switch_cooldown_until
            last_health_status = result.last_health_status
            last_throughput_status = result.last_throughput_status
            failures = result.failures
            slow_checks = result.slow_checks
            quality_slow_checks = result.quality_slow_checks
            throughput_slow_checks = result.throughput_slow_checks
            if result.skip_remaining_loop:
                continue

        if next_candidate_check is not None and time.monotonic() >= next_candidate_check:
            run_random_candidate_check(candidates, args)
            persist_state(args, candidates, active=active_slot.get("candidate"))
            candidates = native_candidate_order(candidates)
            refresh_slot_candidate_refs((active_slot, standby_slot), candidates)
            if not slot_alive(standby_slot) or standby_slot.get("failures", 0) >= args.max_failures:
                rebuild_standby(standby_slot, candidates, slot_candidates(active_slot), args, rules, inject, "candidate check")
            delay = random_check_delay(args)
            next_candidate_check = time.monotonic() + delay
            set_status(
                **status_candidate_fields(candidates, args.standby_max_age),
                active_backend=slot_public_status(active_slot),
                hot_standby=slot_public_status(standby_slot),
                next_candidate_check_at=time.time() + delay,
            )
