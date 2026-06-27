import time
from dataclasses import dataclass

from .failover import evaluate_cooldown, failover_state
from .slot_execution import rebuild_standby
from .slot_manager import (
    check_slot,
    check_slot_large_download,
    slot_alive,
    slot_candidates,
    slot_public_status,
    stop_slot,
    xray_api_status_for_slot,
)
from .state import persist_state
from .status import log, public_candidate, set_status, status_candidate_fields
from .tcp_switch import set_switch_targets
from .telegram import native_recovery_message, send_telegram_notification
from .vless import promote_candidate, quarantine_candidate
from .xray_process import curl_check, throughput_check


@dataclass
class FailoverExecutionResult:
    active_slot: dict
    standby_slot: dict
    candidates: list
    switch_cooldown_until: float
    last_health_status: dict
    last_throughput_status: dict
    failures: int = 0
    slow_checks: int = 0
    quality_slow_checks: int = 0
    throughput_slow_checks: int = 0
    skip_remaining_loop: bool = False


def execute_failover(
    *,
    args,
    decision,
    now,
    switch_cooldown_until,
    failures,
    slow_checks,
    quality_slow_checks,
    throughput_slow_checks,
    active_slot,
    standby_slot,
    switches,
    candidates,
    rules,
    inject,
    last_health_status,
    last_throughput_status,
):
    failover_reason = decision.reason
    cooldown = evaluate_cooldown(decision, switch_cooldown_until, now=now)
    if cooldown.suppressed:
        remaining = cooldown.remaining
        log(f"{failover_reason}; switch suppressed by cooldown for {remaining}s")
        set_status(
            failures=0,
            slow_checks=0,
            quality_slow_checks=0,
            throughput_slow_checks=0,
            switch_cooldown_until=time.time() + remaining,
            failover_state=failover_state(
                decision,
                state="suppressed",
                cooldown_until=switch_cooldown_until,
                now_monotonic=now,
                failures=0,
                slow_checks=0,
                quality_slow_checks=0,
                throughput_slow_checks=0,
            ),
        )
        return FailoverExecutionResult(
            active_slot=active_slot,
            standby_slot=standby_slot,
            candidates=candidates,
            switch_cooldown_until=switch_cooldown_until,
            last_health_status=last_health_status,
            last_throughput_status=last_throughput_status,
            skip_remaining_loop=True,
        )

    set_status(
        failover_state=failover_state(
            decision,
            state="switching",
            cooldown_until=switch_cooldown_until,
            now_monotonic=now,
            failures=failures,
            slow_checks=slow_checks,
            quality_slow_checks=quality_slow_checks,
            throughput_slow_checks=throughput_slow_checks,
        )
    )
    old_active_slot = active_slot
    old_standby_slot = standby_slot
    old_active_candidate = old_active_slot.get("candidate")
    old_active_pool = slot_candidates(old_active_slot)
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
            old_active_pool,
            args,
            rules,
            inject,
            "cold replacement",
        )
        standby_ok, _latency = check_slot(old_standby_slot, args)

    throughput_failover = decision.kind == "throughput_degraded"
    if standby_ok and throughput_failover and args.throughput_check_interval > 0:
        for _attempt in range(2):
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
                old_active_pool,
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
            f"({active_slot['candidate'].get('host')}:{active_slot['candidate'].get('port')}); "
            f"pool={len(slot_candidates(active_slot))}"
        )
        candidates = promote_candidate(candidates, active_slot.get("candidate"))
        stop_slot(standby_slot)
        rebuild_standby(
            standby_slot,
            candidates,
            slot_candidates(active_slot),
            args,
            rules,
            inject,
            "after hot switch",
        )
        persist_state(args, candidates, active=active_slot.get("candidate"))
        switch_cooldown_until = time.monotonic() + args.failover_cooldown
        active_api = xray_api_status_for_slot(active_slot, args)
        standby_api = xray_api_status_for_slot(standby_slot, args)
        set_status(
            **status_candidate_fields(candidates, args.standby_max_age),
            xray_running=slot_alive(active_slot),
            failures=0,
            slow_checks=0,
            quality_slow_checks=0,
            throughput_slow_checks=0,
            switch_cooldown_until=time.time() + args.failover_cooldown,
            active_pool=[public_candidate(candidate) for candidate in slot_candidates(active_slot)],
            active_backend=slot_public_status(active_slot),
            standby_pool=[public_candidate(candidate) for candidate in slot_candidates(standby_slot)],
            hot_standby=slot_public_status(standby_slot),
            active_path=active_api,
            active_observatory=active_api,
            standby_observatory=standby_api,
            last_switch={
                "time": time.time(),
                "reason": failover_reason,
                "kind": decision.kind,
                "standby_used": True,
                "standby_mode": "hot-active",
                "from": old_active_candidate.get("tag") if old_active_candidate else None,
                "to": active_slot["candidate"].get("tag") if active_slot.get("candidate") else None,
            },
            failover_state=failover_state(
                decision,
                state="cooldown",
                cooldown_until=switch_cooldown_until,
                now_monotonic=time.monotonic(),
                failures=0,
                slow_checks=0,
                quality_slow_checks=0,
                throughput_slow_checks=0,
            ),
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
                native_recovery_message(failover_reason, slot_candidates(active_slot), latency, throughput_kbps),
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
        return FailoverExecutionResult(
            active_slot=active_slot,
            standby_slot=standby_slot,
            candidates=candidates,
            switch_cooldown_until=switch_cooldown_until,
            last_health_status=last_health_status,
            last_throughput_status=last_throughput_status,
            skip_remaining_loop=True,
        )

    log(f"{failover_reason}; no healthy standby was available")
    set_status(failures=0, slow_checks=0, quality_slow_checks=0, throughput_slow_checks=0)
    set_status(
        failover_state=failover_state(
            decision,
            state="failed",
            cooldown_until=switch_cooldown_until,
            now_monotonic=time.monotonic(),
            failures=0,
            slow_checks=0,
            quality_slow_checks=0,
            throughput_slow_checks=0,
        )
    )
    return FailoverExecutionResult(
        active_slot=active_slot,
        standby_slot=standby_slot,
        candidates=candidates,
        switch_cooldown_until=switch_cooldown_until,
        last_health_status=last_health_status,
        last_throughput_status=last_throughput_status,
    )
