import time


def initial_schedule(args, startup_subscription_status, checks_enabled):
    now = time.monotonic()
    retry_delay = None
    if startup_subscription_status.get("status") == "ok" or args.sub_post_start_refresh_delay <= 0:
        next_refresh = now + args.refresh_interval
    else:
        retry_delay = args.sub_post_start_refresh_delay
        next_refresh = now + retry_delay
    return {
        "now": now,
        "subscription_retry_delay": retry_delay,
        "next_asset_refresh": now + args.asset_refresh_interval if args.asset_refresh_interval > 0 else None,
        "next_refresh": next_refresh,
        "next_health_check": now + 3,
        "next_quality_check": now + 12 if args.quality_check_interval > 0 else None,
        "next_throughput_check": now + 8 if args.throughput_check_interval > 0 else None,
        "next_diagnostics_check": now + 2,
        "next_active_path_check": now + 2,
        "next_candidate_check": None,
        "candidate_checks_enabled": checks_enabled,
    }


def due_times(
    args,
    *,
    next_refresh,
    next_health_check,
    next_diagnostics_check,
    next_active_path_check,
    next_asset_refresh=None,
    next_quality_check=None,
    next_throughput_check=None,
    next_candidate_check=None,
):
    times = [next_refresh, next_health_check, next_diagnostics_check]
    if args.active_path_interval > 0:
        times.append(next_active_path_check)
    for value in (
        next_asset_refresh,
        next_quality_check,
        next_throughput_check,
        next_candidate_check,
    ):
        if value is not None:
            times.append(value)
    return times


def loop_sleep_seconds(times, now=None):
    now = time.monotonic() if now is None else now
    return max(0.1, min(times) - now)
