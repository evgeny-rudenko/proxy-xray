"""Amsterdam route supervisor. Network probes execute sequentially."""
from collections import deque
import random
import signal
import socket
import subprocess
import sys
import time

from .assets import prepare_assets, refresh_assets
from .chain_config import DNS_PROXY_PORT, SLOTS
from .chain_config import make_chain_config
from .chain_process import ChainCore
from .dns_runtime import set_dns_proxy_ready, write_bootstrap_domains
from .health import build_health_checks
from .route_inventory import RouteInventory
from .route_probe import health_at
from .route_control import apply_route_policy, queue_recheck
from .route_status import publish
from .routes import DIRECT, CHAIN, SUBSCRIPTION, UNAVAILABLE, RoutePolicy, normalize_route_policy
from .state import load_state, persist_state
from .status import log, mark_control_command, pop_control_commands, set_status, status_snapshot
from .subscription import load_candidates, load_extra_candidates
from .telegram import send_telegram_notification
from .tcp_switch import TcpSwitch, set_switch_targets, start_switches, stop_switches
from .xray_process import terminate_process, throughput_check
from .xray_config import config_fingerprint


class Supervisor:
    def __init__(self, args):
        self.args = args
        self.policy = RoutePolicy(interval=args.check_interval, max_failures=args.max_failures,
                                  return_successes=args.return_successes, hold=args.return_hold,
                                  direct_return_stable=args.direct_return_stable,
                                  direct_retry_delay=args.direct_retry_delay, direct_retry_max=args.direct_retry_max,
                                  direct_failure_window=args.direct_failure_window)
        self.core = None
        self.switches = {}
        self.jobs = deque()
        self.stopping = False
        self.backoff = 1
        self.next_restart = 0
        self.dead_seen = False
        self.core_started = 0
        self.next_candidate = 0
        self.control = None
        self.notifications = deque(maxlen=5)

    def stop(self, *_):
        self.stopping = True

    def save(self):
        persist_state(self.args, self.inventory.candidates,
                      route_history=self.inventory.history, route_mode=self.policy.mode,
                      route_policy=self.policy.route_policy)

    def set_mode(self, mode, reason):
        if not self.policy.allows(mode):
            log("[chain] forbidden route override rejected")
            return False
        if not self.core or not self.core.override(mode):
            log("[chain] route override failed; no transition committed")
            return False
        previous = self.policy.mode
        self.policy.commit(mode, reason)
        set_dns_proxy_ready(self.args, mode != UNAVAILABLE)
        set_status(last_switch={"time": time.time(), "from": previous, "to": mode, "reason": reason})
        log(f"[chain] route {previous} -> {mode}: {reason}")
        if previous == DIRECT and mode != DIRECT and self.policy.direct_retry_at > time.monotonic():
            log(f"[chain] direct return delayed {self.policy.direct_recovery()['retry_remaining']}s after rapid failure")
        if previous != UNAVAILABLE and self.args.telegram_bot_token:
            self.notifications.append(f"proxy-xray route: {previous} -> {mode}\n{reason}")
        self.save()
        return True

    def rebuild(self):
        old = self.core
        known = {c["uri"] for c in self.inventory.candidates}
        retired = bool(old and any(c["uri"] not in known for c in old.mapping.values()))
        if old and old.alive():
            planned, mapping = make_chain_config(self.inventory.exits, self.inventory.pool(), self.args,
                                                 old.slot, self.inventory.direct, self.inventory.pairs)
            if config_fingerprint(planned) == config_fingerprint(old.config):
                old.mapping = mapping
                return True
        slot = SLOTS[1] if old and old.slot["api_port"] == SLOTS[0]["api_port"] else SLOTS[0]
        staged = ChainCore(self.args, slot, self.inventory.exits, self.inventory.pool(),
                           self.inventory.direct, self.inventory.pairs)
        try:
            staged.start()
            desired = self.policy.mode if self.policy.allows(self.policy.mode) else UNAVAILABLE
            if desired != UNAVAILABLE:
                result = health_at(slot["probe_ports"][desired], self.args)
                if not result.ok:
                    if old and old.alive() and not retired:
                        log("[chain] staged configuration failed preflight; keeping old core")
                        staged.stop()
                        return False
                    desired = UNAVAILABLE
                    for mode in self.policy.priority:
                        result = health_at(slot["probe_ports"][mode], self.args)
                        if result.ok:
                            desired = mode
                            break
            if not staged.override(desired):
                raise RuntimeError("staged route override failed")
            if not self.switches:
                self.switches = start_switches(slot, self.args)
                dns = TcpSwitch("Global DNS", "127.0.0.1", DNS_PROXY_PORT, target_port=slot["dns_port"])
                dns.start()
                self.switches["dns"] = dns
            else:
                set_switch_targets(self.switches, slot)
                self.switches["dns"].set_target("127.0.0.1", slot["dns_port"])
            self.core = staged
            self.core_started = time.monotonic()
            if old:
                old.stop()
            if desired != self.policy.mode:
                self.set_mode(desired, "core restarted; route reverified")
            else:
                set_dns_proxy_ready(self.args, desired != UNAVAILABLE)
            log("[chain] staged configuration activated; one working core")
            return True
        except Exception as exc:
            staged.stop()
            log(f"[chain] core preparation failed ({type(exc).__name__})")
            return False

    def check_routes(self):
        current = self.policy.mode
        priority = self.policy.priority
        modes = [current] if current != UNAVAILABLE else []
        modes.extend(m for m in priority if m not in modes and (
            current == UNAVAILABLE or current not in priority or priority.index(m) < priority.index(current)))
        if self.policy.failures >= self.args.max_failures - 1:
            modes.extend(m for m in priority if m not in modes)
        modes = [mode for mode in modes if self.policy.allows(mode)]
        for mode in modes:
            if mode in (CHAIN, SUBSCRIPTION) and not self.inventory.pool():
                continue
            result = health_at(self.core.slot["probe_ports"][mode], self.args)
            self.policy.observe(mode, result.ok, result.latency)
            if mode == current:
                set_status(last_health={"time": result.time, "status": "ok" if result.ok else "fail",
                                        "latency": result.latency})
        desired = self.policy.choose()
        if desired != self.policy.mode:
            failed = self.policy.failures >= self.args.max_failures
            reason = ("Amsterdam route failed; subscription exit verified" if desired == SUBSCRIPTION
                      else f"{current} failed; verified alternate route" if failed and desired != UNAVAILABLE
                      else "preferred route verified" if desired != UNAVAILABLE
                      else "no working foreign route; RU remains direct")
            self.set_mode(desired, reason)
            if desired in (SUBSCRIPTION, UNAVAILABLE):
                self.queue_matrix()

    def queue_matrix(self):
        requested = self.inventory.diagnostic_pairs()
        if self.policy.allows(DIRECT):
            requested += [(DIRECT, c, None) for c in self.inventory.exits[:2]]
        keys = {(mode, c["uri"], relay["uri"] if relay else None) for mode, c, relay in self.jobs}
        for job in requested:
            mode, candidate, relay = job
            key = (mode, candidate["uri"], relay["uri"] if relay else None)
            if key not in keys:
                self.jobs.append(job)
                keys.add(key)

    def run_probe_job(self):
        mode, candidate, relay = self.jobs.popleft()
        if not self.policy.allows(mode):
            return
        previous_pool = [c["uri"] for c in self.inventory.pool()]
        previous_direct = set(self.inventory.direct)
        previous_pairs = set(self.inventory.pairs)
        result = self.inventory.probe(mode, candidate, relay)
        if result.ok and mode != self.policy.mode:
            self.policy.observe(mode, True, result.latency, recovery=False)
        changed = (previous_pool != [c["uri"] for c in self.inventory.pool()]
                   or previous_direct != self.inventory.direct
                   or previous_pairs != self.inventory.pairs)
        if changed:
            self.rebuild()
        if self.policy.mode == UNAVAILABLE:
            desired = self.policy.choose()
            if desired != UNAVAILABLE:
                actual = health_at(self.core.slot["probe_ports"][desired], self.args)
                if actual.ok:
                    self.policy.observe(desired, True, actual.latency)
                    self.set_mode(desired, "startup route verified end to end")
        if mode == SUBSCRIPTION and result.ok:
            pool_uris = {c["uri"] for c in self.inventory.pool()}
            if self.policy.mode != DIRECT or not any(r in pool_uris for _, r in self.inventory.pairs):
                self.queue_matrix()
        self.save()

    def controls(self):
        for command in pop_control_commands():
            if command["name"] == "route_policy":
                apply_route_policy(self, command)
                continue
            if command["name"] != "recheck_route":
                mark_control_command(command, "failed", "unsupported command")
                continue
            if self.control:
                mark_control_command(command, "coalesced", "route recheck already running")
                continue
            self.control = command
            mark_control_command(command, "running", "sequential route recheck")
            queue_recheck(self)

    def refresh(self):
        try:
            candidates = load_candidates(self.args, self.inventory.candidates, load_state(self.args.state_file),
                                         allow_proxy=self.policy.mode != UNAVAILABLE)
            self.inventory.update(candidates)
            known = {c["uri"] for c in candidates}
            self.jobs = deque(job for job in self.jobs if job[1]["uri"] in known
                              and (job[2] is None or job[2]["uri"] in known))
            write_bootstrap_domains(self.args, candidates)
            for c in self.inventory.exits + self.inventory.pool():
                self.inventory.resolver.resolve(c)
            self.rebuild()
            set_status(last_refresh_at=time.time())
            self.save()
        except Exception as exc:
            log(f"[chain] subscription refresh unavailable ({type(exc).__name__}); retaining current route")

    def run(self):
        args = self.args
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        set_dns_proxy_ready(args, False)
        prepare_assets(args)
        if args.asset_refresh_on_start:
            refresh_assets(args, "startup")
        write_bootstrap_domains(args, load_extra_candidates(args))
        dns = subprocess.Popen([sys.executable, "/dns-split-proxy.py", "--dns-global", args.dns_global,
                                "--dns-ru", args.dns_ru, "--global-route", "socks",
                                "--proxy-socks-port", str(DNS_PROXY_PORT), "--no-global-direct-fallback",
                                "--bootstrap-file", args.dns_bootstrap_file, "--status-file", args.dns_status_file,
                                "--proxy-ready-file", args.dns_proxy_ready_file])
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", 53), timeout=0.2):
                        break
                except OSError:
                    if dns.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("split DNS did not become ready")
                    time.sleep(0.1)
            with open("/etc/resolv.conf", "w", encoding="ascii") as handle:
                handle.write("nameserver 127.0.0.1\noptions timeout:2 attempts:1\n")
            state = load_state(args.state_file)
            self.policy.route_policy = normalize_route_policy(state.get("route_policy"))
            set_status(route_policy=self.policy.route_policy)
            candidates = load_candidates(args, state=state, allow_proxy=False)
            self.inventory = RouteInventory(args, candidates, state.get("route_history"))
            if not self.inventory.exits:
                raise RuntimeError("vless-extra.txt must contain Amsterdam exit transports")
            write_bootstrap_domains(args, candidates)
            if not self.rebuild():
                raise RuntimeError("initial core could not start")
            self.jobs.extend(job for job in self.inventory.startup_jobs() if self.policy.allows(job[0]))
            now = time.monotonic()
            next_health = next_status = now
            next_quality, next_speed = now + 60, now + 30
            next_refresh = now + (args.refresh_interval if (status_snapshot().get("last_subscription_status") or {}).get("status") == "ok"
                                  else args.sub_post_start_refresh_delay)
            next_assets = now + args.asset_refresh_interval
            self.next_candidate = now + random.uniform(args.candidate_check_min_interval, args.candidate_check_max_interval)
            while not self.stopping:
                now = time.monotonic()
                self.controls()
                if not self.core.alive():
                    set_dns_proxy_ready(args, False)
                    if not self.dead_seen:
                        self.dead_seen = True
                        self.next_restart = now + self.backoff
                        log(f"[chain] Xray stopped; restarting in {self.backoff}s")
                        self.backoff = min(30, self.backoff * 2)
                        publish(args, self.inventory, self.policy, self.core, None)
                    if now >= self.next_restart:
                        if self.rebuild():
                            self.dead_seen = False
                            next_health = 0
                        else:
                            self.next_restart = time.monotonic() + self.backoff
                            self.backoff = min(30, self.backoff * 2)
                    time.sleep(0.2)
                    continue
                if now - self.core_started >= 60:
                    self.backoff = 1
                if dns.poll() is not None:
                    raise RuntimeError("split DNS process stopped")
                if now >= next_health:
                    self.check_routes()
                    next_health = time.monotonic() + args.check_interval
                    set_status(next_route_check_at=time.time() + args.check_interval)
                    snapshot = status_snapshot()
                    checks = build_health_checks(args, self.core.alive(), snapshot.get("last_health"),
                                                 snapshot.get("last_quality"), snapshot.get("last_throughput"),
                                                 snapshot.get("last_subscription_status"))
                    for key in ("quality_download", "throughput"):
                        if checks[key]["status"] == "fail":
                            checks[key]["status"] = "warn"
                    set_status(health_checks=checks, last_health_checks_at=time.time())
                if now >= next_refresh:
                    self.refresh()
                    next_refresh = time.monotonic() + args.refresh_interval
                if now >= self.next_candidate and not self.jobs:
                    candidates = self.inventory.relays if (not self.policy.allows(DIRECT) or random.random() < 0.6) and self.inventory.relays else self.inventory.exits
                    if candidates:
                        candidate = random.choice(candidates)
                        mode = SUBSCRIPTION if candidate["source"] == "subscription" else DIRECT
                        if self.policy.allows(mode):
                            self.jobs.append((mode, candidate, None))
                    if self.policy.mode != DIRECT:
                        self.queue_matrix()
                    self.next_candidate = time.monotonic() + random.uniform(args.candidate_check_min_interval, args.candidate_check_max_interval)
                if self.jobs:
                    self.run_probe_job()
                if self.control and not self.jobs:
                    mark_control_command(self.control, "done", "recheck complete; selected policy applied")
                    self.control = None
                    next_health = 0
                if self.notifications and not self.jobs:
                    send_telegram_notification(args, self.notifications.popleft())
                if self.policy.mode != UNAVAILABLE:
                    for kind, due, url, timeout, minimum in (
                        ("quality", next_quality, args.quality_url, args.quality_max_time, args.quality_min_kbps),
                        ("throughput", next_speed, args.throughput_url, args.throughput_max_time, args.throughput_min_kbps),
                    ):
                        interval = args.quality_check_interval if kind == "quality" else args.throughput_check_interval
                        if interval > 0 and now >= due:
                            ok, kbps = throughput_check(1080, url, timeout)
                            set_status(**{"last_" + kind: {"time": time.time(), "status": "ok" if ok and kbps >= minimum else "warn",
                                                           "kbps": round(kbps) if ok else None}})
                            if kind == "quality":
                                next_quality = time.monotonic() + interval
                            else:
                                next_speed = time.monotonic() + interval
                if args.asset_refresh_interval > 0 and now >= next_assets:
                    if refresh_assets(args):
                        self.rebuild()
                    next_assets = time.monotonic() + args.asset_refresh_interval
                if now >= next_status:
                    publish(args, self.inventory, self.policy, self.core,
                            time.time() + max(0, self.next_candidate - time.monotonic()))
                    next_status = time.monotonic() + args.active_path_interval
                time.sleep(0.1)
        finally:
            set_dns_proxy_ready(args, False)
            stop_switches(self.switches)
            if self.core:
                self.core.stop()
            terminate_process(dns)


def run(args):
    Supervisor(args).run()
