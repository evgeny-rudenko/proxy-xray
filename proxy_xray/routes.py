"""Route policy, independent of processes, DNS and the web server."""
from dataclasses import dataclass, field
import math
import time

DIRECT = "direct_amsterdam"
CHAIN = "chained_amsterdam"
SUBSCRIPTION = "subscription_exit"
UNAVAILABLE = "unavailable"
PRIORITY = (DIRECT, CHAIN, SUBSCRIPTION)
AUTO = "auto"
SUBSCRIPTION_ONLY = "subscription_only"
POLICIES = (AUTO, SUBSCRIPTION_ONLY)


def normalize_route_policy(value):
    return value if value in POLICIES else AUTO


def allowed_routes(policy):
    return (CHAIN, SUBSCRIPTION) if policy == SUBSCRIPTION_ONLY else PRIORITY
ROUTE_TAGS = {DIRECT: "route-direct", CHAIN: "route-chain", SUBSCRIPTION: "route-subscription", UNAVAILABLE: "block"}


def split_roles(candidates):
    return (
        [c for c in candidates if c.get("source") == "extra"],
        [c for c in candidates if c.get("source") == "subscription"],
    )


def relay_pool(candidates, verified, size=3):
    from .vless import native_candidate_order
    eligible = [c for c in candidates if c.get("source") == "subscription" and c["uri"] in verified]
    ordered = native_candidate_order(eligible)
    unique, duplicates, hosts = [], [], set()
    for c in ordered:
        if c["host"] in hosts:
            duplicates.append(c)
        else:
            unique.append(c)
            hosts.add(c["host"])
    return (unique + duplicates)[:size]


@dataclass
class RoutePolicy:
    route_policy: str = AUTO
    interval: float = 20
    max_failures: int = 2
    return_successes: int = 3
    hold: float = 180
    direct_return_stable: float = 300
    direct_retry_delay: float = 600
    direct_retry_max: float = 900
    direct_failure_window: float = 180
    mode: str = UNAVAILABLE
    switched_at: float = 0
    failures: int = 0
    checks: dict = field(default_factory=dict)
    success_streaks: dict = field(default_factory=dict)
    last_success_times: dict = field(default_factory=dict)
    healthy_since: dict = field(default_factory=dict)
    direct_retry_at: float = 0
    direct_fast_failures: int = 0
    reason: str = "startup: routes not verified"

    @property
    def priority(self):
        return allowed_routes(self.route_policy)

    def allows(self, mode):
        return mode == UNAVAILABLE or mode in self.priority

    def observe(self, mode, ok, latency=None, now=None, recovery=True):
        now = time.monotonic() if now is None else now
        self.checks[mode] = {"ok": bool(ok), "latency": latency, "time": time.time(), "monotonic": now}
        previous = self.last_success_times.get(mode, -1e12)
        if ok and recovery:
            if now - previous >= self.interval * 0.9:
                if mode not in self.healthy_since or now - previous > self.interval * 2.5:
                    self.healthy_since[mode] = now
                self.success_streaks[mode] = (
                    self.success_streaks.get(mode, 0) + 1 if now - previous <= self.interval * 2.5 else 1
                )
                self.last_success_times[mode] = now
        elif not ok:
            self.success_streaks[mode] = 0
            self.healthy_since.pop(mode, None)
        if mode == self.mode:
            self.failures = 0 if ok else self.failures + 1
            stable_since = max(self.switched_at, self.healthy_since.get(DIRECT, now))
            if mode == DIRECT and ok and now - stable_since >= self.direct_return_stable:
                self.direct_fast_failures = 0
                self.direct_retry_at = 0

    def direct_recovery(self, now=None):
        now = time.monotonic() if now is None else now
        since = self.healthy_since.get(DIRECT)
        latest = self.last_success_times.get(DIRECT, -1e12)
        elapsed = max(0, now - since) if since is not None and now - latest <= self.interval * 2.5 else 0
        return {"stable_seconds": int(elapsed), "required_stable_seconds": self.direct_return_stable,
                "retry_remaining": max(0, math.ceil(self.direct_retry_at - now)),
                "stable_remaining": max(0, math.ceil(self.direct_return_stable - elapsed)),
                "fast_failures": self.direct_fast_failures}

    def healthy(self, mode, now):
        check = self.checks.get(mode, {})
        return check.get("ok", False) and now - check.get("monotonic", -1e12) <= self.interval * 3

    def choose(self, now=None):
        now = time.monotonic() if now is None else now
        priority = self.priority
        available = [mode for mode in priority if self.healthy(mode, now)]
        if self.mode == UNAVAILABLE or not self.allows(self.mode) or self.failures >= self.max_failures:
            return available[0] if available else UNAVAILABLE
        for mode in available:
            if priority.index(mode) >= priority.index(self.mode):
                break
            if mode == DIRECT:
                recovery = self.direct_recovery(now)
                if now < self.direct_retry_at or recovery["stable_seconds"] < self.direct_return_stable:
                    continue
            if now - self.switched_at >= self.hold and self.success_streaks.get(mode, 0) >= self.return_successes:
                return mode
        return self.mode

    def commit(self, mode, reason, now=None):
        now = time.monotonic() if now is None else now
        if self.mode == DIRECT and mode != DIRECT:
            if self.failures >= self.max_failures and now - self.switched_at <= self.direct_failure_window:
                self.direct_fast_failures += 1
                delay = min(self.direct_retry_max, self.direct_retry_delay * self.direct_fast_failures)
                self.direct_retry_at = now + delay
            self.healthy_since.pop(DIRECT, None)
            self.last_success_times.pop(DIRECT, None)
            self.success_streaks[DIRECT] = 0
        self.mode = mode
        self.switched_at = now
        self.failures = 0
        self.reason = reason

    def public_checks(self):
        return {mode: {key: value for key, value in item.items() if key != "monotonic"}
                for mode, item in self.checks.items()}

