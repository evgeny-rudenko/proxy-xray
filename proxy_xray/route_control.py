"""Operator policy changes run on the supervisor's sequential worker."""
from collections import deque

from .dns_runtime import set_dns_proxy_ready
from .routes import DIRECT, POLICIES, UNAVAILABLE
from .state import persist_route_policy
from .status import log, mark_control_command, set_status


def apply_route_policy(supervisor, command):
    value = command["payload"].get("policy")
    if value not in POLICIES:
        mark_control_command(command, "failed", "invalid routing policy")
        return
    if value == supervisor.policy.route_policy:
        mark_control_command(command, "done", "routing policy already applied")
        return
    mark_control_command(command, "running", "saving routing policy")
    try:
        persist_route_policy(supervisor.args, value)
    except Exception as exc:
        log(f"[chain] routing policy save failed ({type(exc).__name__}); unchanged")
        mark_control_command(command, "failed", "cannot save routing policy; unchanged")
        return
    previous = supervisor.policy.route_policy
    supervisor.policy.route_policy = value
    set_status(route_policy=value)
    supervisor.jobs = deque(job for job in supervisor.jobs if supervisor.policy.allows(job[0]))
    log(f"[chain] routing policy {previous} -> {value}")
    if not supervisor.policy.allows(supervisor.policy.mode):
        if not supervisor.set_mode(UNAVAILABLE, "operator policy: direct Amsterdam disabled"):
            # If Routing API cannot close the forbidden path, stop it before recovery.
            supervisor.core.stop()
            supervisor.policy.commit(UNAVAILABLE, "routing API failed; forbidden route stopped")
            set_dns_proxy_ready(supervisor.args, False)
            supervisor.save()
            mark_control_command(command, "failed", "policy saved; core stopped for safe recovery")
            return
    supervisor.queue_matrix()
    if supervisor.core and supervisor.core.alive():
        supervisor.check_routes()
    mark_control_command(command, "done", "routing policy applied")


def queue_recheck(supervisor):
    if supervisor.policy.allows(DIRECT):
        supervisor.jobs.extend((DIRECT, c, None) for c in supervisor.inventory.exits
                              if not any(job[1]["uri"] == c["uri"] and job[0] == DIRECT
                                         for job in supervisor.jobs))
    supervisor.queue_matrix()
