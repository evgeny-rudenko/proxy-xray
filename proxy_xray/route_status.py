"""Secret-free publication of the route graph and its health."""
import time

from .chain_config import BALANCERS, chain_relay_balancer
from .dns_runtime import dns_transport_snapshot
from .health import health_item
from .routes import DIRECT, CHAIN, SUBSCRIPTION, UNAVAILABLE
from .status import public_candidate, set_status, status_candidate_fields, status_config_fields, status_snapshot


def publish(args, inventory, policy, core, next_check, rebuilding=False):
    selections = {}
    api_ok = False
    if core and core.alive():
        for mode, balancer in BALANCERS.items():
            tag, candidate, ok = core.selected(balancer)
            selections[mode] = {"tag": tag, "candidate": public_candidate(candidate) if candidate else None,
                                "status": "ok" if ok else "unknown"}
            api_ok |= ok
    selected = selections.get(policy.mode, {})
    relay = selections.get(SUBSCRIPTION, {}) if policy.mode in (CHAIN, SUBSCRIPTION) else {}
    exit_candidate = selected.get("candidate")
    if policy.mode == CHAIN:
        relay = {}
        if exit_candidate and core and core.alive():
            tag, candidate, ok = core.selected(chain_relay_balancer(exit_candidate))
            relay = {"tag": tag, "candidate": public_candidate(candidate) if candidate else None,
                     "status": "ok" if ok else "unknown"}
    snapshot = status_snapshot()
    now = time.monotonic()
    checks = dict(snapshot.get("health_checks") or {})
    checks["xray_process"] = health_item("ok" if core and core.alive() else "fail", "Xray process",
                                         "running" if core and core.alive() else "stopped")
    checks["route"] = health_item("fail" if policy.mode == UNAVAILABLE else "warn" if policy.mode == SUBSCRIPTION else "ok",
                                  "Exit route", policy.reason if policy.mode == SUBSCRIPTION else policy.mode)
    set_status(
        **status_candidate_fields(inventory.candidates),
        **(status_config_fields(core.config) if core else {}),
        mode="amsterdam-chain", route_mode=policy.mode, route_policy=policy.route_policy,
        exit={"location": "Amsterdam (configured)" if policy.mode in (DIRECT, CHAIN) else "Subscription exit"
              if policy.mode == SUBSCRIPTION else "UNAVAILABLE", "transport": exit_candidate,
              "selected_tag": selected.get("tag") or "UNKNOWN"},
        relay={"transport": relay.get("candidate"), "selected_tag": relay.get("tag") or "UNKNOWN"},
        route_checks=policy.public_checks(), route_selections=selections,
        route_transition={"reason": policy.reason, "time": snapshot.get("last_switch", {}).get("time")
                          if snapshot.get("last_switch") else None,
                          "return_successes": dict(policy.success_streaks),
                          "required_successes": policy.return_successes,
                          "hold_remaining": max(0, round(policy.hold - (now - policy.switched_at))),
                          "direct_recovery": policy.direct_recovery(now),
                          "rebuilding": rebuilding},
        exit_pool=[public_candidate(c) for c in inventory.exits],
        relay_pool=[public_candidate(c) for c in inventory.pool()],
        xray_running=bool(core and core.alive()), failures=policy.failures,
        active_path={"status": "ok" if api_ok else "unknown", "selected": exit_candidate,
                     "selected_tag": selected.get("tag"), "strategy": "leastPing"},
        next_candidate_check_at=next_check, health_checks=checks,
        dns_transport=dns_transport_snapshot(args),
    )

