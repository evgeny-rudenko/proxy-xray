import time
from dataclasses import dataclass


@dataclass(frozen=True)
class FailoverDecision:
    triggered: bool
    kind: str = "none"
    reason: str = ""
    full_failure: bool = False
    standby_ready: bool = False


@dataclass(frozen=True)
class CooldownDecision:
    suppressed: bool
    remaining: int = 0


def evaluate_failover(
    args,
    failures=0,
    slow_checks=0,
    quality_slow_checks=0,
    throughput_slow_checks=0,
    standby_ready=False,
):
    if failures >= args.max_failures:
        return FailoverDecision(
            triggered=True,
            kind="connection_failed",
            reason=f"connection failed {failures}/{args.max_failures}",
            full_failure=True,
            standby_ready=standby_ready,
        )

    if args.hot_standby_fast_failures > 0 and failures >= args.hot_standby_fast_failures and standby_ready:
        return FailoverDecision(
            triggered=True,
            kind="active_path_failed",
            reason=f"active path failed {failures}/{args.max_failures}; healthy hot standby is ready",
            full_failure=True,
            standby_ready=standby_ready,
        )

    if args.degrade_checks > 0 and slow_checks >= args.degrade_checks:
        return FailoverDecision(
            triggered=True,
            kind="latency_degraded",
            reason=f"connection degraded {slow_checks}/{args.degrade_checks}; latency >= {args.degrade_latency:.3f}s",
            full_failure=False,
            standby_ready=standby_ready,
        )

    if args.quality_degrade_checks > 0 and quality_slow_checks >= args.quality_degrade_checks:
        return FailoverDecision(
            triggered=True,
            kind="quality_degraded",
            reason=(
                f"quality degraded {quality_slow_checks}/{args.quality_degrade_checks}; "
                f"speed < {args.quality_min_kbps:.0f} kbps"
            ),
            full_failure=False,
            standby_ready=standby_ready,
        )

    if args.throughput_degrade_checks > 0 and throughput_slow_checks >= args.throughput_degrade_checks:
        return FailoverDecision(
            triggered=True,
            kind="throughput_degraded",
            reason=(
                f"throughput degraded {throughput_slow_checks}/{args.throughput_degrade_checks}; "
                f"speed < {args.throughput_min_kbps:.0f} kbps"
            ),
            full_failure=False,
            standby_ready=standby_ready,
        )

    return FailoverDecision(triggered=False, standby_ready=standby_ready)


def evaluate_cooldown(decision, cooldown_until=0.0, now=None):
    now = time.monotonic() if now is None else now
    if not decision.triggered or decision.full_failure:
        return CooldownDecision(suppressed=False)
    if cooldown_until and now < cooldown_until:
        return CooldownDecision(suppressed=True, remaining=max(1, int(cooldown_until - now)))
    return CooldownDecision(suppressed=False)


def failover_state(
    decision=None,
    state="idle",
    cooldown_until=0.0,
    now_monotonic=None,
    standby_ready=False,
    failures=0,
    slow_checks=0,
    quality_slow_checks=0,
    throughput_slow_checks=0,
):
    now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    decision = decision or FailoverDecision(triggered=False, standby_ready=standby_ready)
    remaining = max(0, int(cooldown_until - now_monotonic)) if cooldown_until else 0
    return {
        "state": state,
        "kind": decision.kind,
        "reason": decision.reason or None,
        "full_failure": bool(decision.full_failure),
        "standby_ready": bool(decision.standby_ready),
        "cooldown_remaining": remaining,
        "failures": failures,
        "slow_checks": slow_checks,
        "quality_slow_checks": quality_slow_checks,
        "throughput_slow_checks": throughput_slow_checks,
        "updated_at": time.time(),
    }
