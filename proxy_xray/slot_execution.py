import time

from .pool import select_active_pool, select_standby_pool
from .slot_manager import (
    candidate_label,
    check_slot,
    normalize_slot_candidates,
    slot_candidates,
    start_slot,
    stop_slot,
)
from .status import log
from .vless import native_candidate_order, quarantine_candidate


def rebuild_standby(standby_slot, candidates, active_pool, args, rules, inject, reason):
    active_pool = normalize_slot_candidates(active_pool)
    pool = select_standby_pool(
        candidates,
        active_pool=active_pool,
        size=getattr(args, "standby_pool_size", 1),
        max_age=args.standby_max_age,
        extra_reserve_per_slot=getattr(args, "pool_extra_reserve_per_slot", 0),
        extra_require_live=getattr(args, "pool_extra_require_live", True),
        extra_max_age=0,
        extra_max_per_host=getattr(args, "pool_extra_max_per_host", 1),
    )
    if not pool:
        log(f"{reason}; no candidate available for hot standby")
        stop_slot(standby_slot)
        return None
    mode = "pool"
    fallback = pool[0]
    log(
        f"{reason}; starting hot standby from {mode}: "
        f"{candidate_label(fallback)} ({fallback.get('host')}:{fallback.get('port')}); "
        f"pool={len(pool)}"
    )
    start_slot(standby_slot, pool, args, rules, inject, active=False)
    return pool


def start_active_with_preflight(active_slot, candidates, args, rules, inject, reason, attempts=None):
    attempt_count = attempts or max(
        3,
        getattr(args, "active_pool_size", 1) + getattr(args, "standby_pool_size", 1),
    )
    warmup_delay = max(0.0, float(getattr(args, "candidate_check_start_delay", 0.0)))
    for attempt in range(max(1, attempt_count)):
        pool = select_active_pool(
            candidates,
            size=getattr(args, "active_pool_size", 1),
            extra_reserve_per_slot=getattr(args, "pool_extra_reserve_per_slot", 0),
            extra_require_live=getattr(args, "pool_extra_require_live", True),
            extra_max_age=0,
            extra_max_per_host=getattr(args, "pool_extra_max_per_host", 1),
        )
        if not pool:
            log(f"{reason}; no candidate available for active pool")
            stop_slot(active_slot)
            return False

        start_slot(active_slot, pool, args, rules, inject, active=True)
        if warmup_delay > 0:
            time.sleep(warmup_delay)
        ok, latency = check_slot(active_slot, args)
        if ok:
            log(
                f"{reason}; active pool ready via {candidate_label(active_slot['candidate'])} "
                f"({latency:.3f}s); pool={len(slot_candidates(active_slot))}"
            )
            return True

        failed = active_slot.get("candidate")
        if failed:
            quarantine_candidate(
                failed,
                args.quarantine_duration,
                f"{reason} active preflight failed",
            )
            log(
                f"{reason}; active preflight failed for {candidate_label(failed)} "
                f"({attempt + 1}/{max(1, attempt_count)}), trying another fallback"
            )
        stop_slot(active_slot)
        candidates = native_candidate_order(candidates)
    return False
