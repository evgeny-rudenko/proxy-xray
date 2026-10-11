"""Session proofs and separate histories for exits, relays and pinned pairs."""
import hashlib
import time

from .endpoint_dns import EndpointResolver
from .route_probe import Probe, pinned_probe
from .routes import DIRECT, CHAIN, SUBSCRIPTION, relay_pool, split_roles
from .status import log
from .vless import assign_candidate_tags, native_candidate_order


class RouteInventory:
    def __init__(self, args, candidates, history=None):
        self.args = args
        self.resolver = EndpointResolver(args)
        self.direct = set()
        self.verified_relays = set()
        self.pairs = set()
        self.history = dict(list(history.items())[-512:]) if isinstance(history, dict) else {}
        self.update(candidates)

    def update(self, candidates):
        assign_candidate_tags(candidates)
        self.candidates = candidates
        self.exits, self.relays = split_roles(candidates)
        known_exits = {c["uri"] for c in self.exits}
        known_relays = {c["uri"] for c in self.relays}
        self.direct &= known_exits
        self.verified_relays &= known_relays
        self.pairs = {(e, r) for e, r in self.pairs if e in known_exits and r in known_relays}
        self.exits.sort(key=lambda c: {"grpc": 0, "xhttp": 1}.get(c["outbound"].get("streamSettings", {}).get("network"), 2))

    def pool(self):
        return relay_pool(self.relays, self.verified_relays, self.args.relay_pool_size)

    def probe(self, mode, candidate, relay=None):
        if mode == CHAIN:
            resolved = self.resolver.resolve(candidate, relay=relay)
        else:
            resolved = self.resolver.resolve(candidate)
        if not resolved:
            log(f"[chain] endpoint DNS unavailable; skipped {candidate['tag']} without candidate penalty")
            return Probe(False, reason="endpoint DNS unavailable; not tested", time=time.time())
        result = pinned_probe(candidate, self.args, relay=relay, repeat=2 if mode == DIRECT else 1)
        key = hashlib.sha256((mode + candidate["uri"] + (relay["uri"] if relay else "")).encode()).hexdigest()
        previous = self.history.get(key, {})
        records = previous.get("checks", []) if isinstance(previous, dict) else []
        if not isinstance(records, list):
            records = []
        self.history[key] = {"role": mode, "tag": candidate["tag"], "relay_tag": relay["tag"] if relay else None,
                             "checks": (records + [vars(result)])[-50:]}
        if len(self.history) > 512:
            self.history.pop(next(iter(self.history)))
        if mode == DIRECT:
            (self.direct.add if result.ok else self.direct.discard)(candidate["uri"])
        elif mode == SUBSCRIPTION:
            (self.verified_relays.add if result.ok else self.verified_relays.discard)(candidate["uri"])
        else:
            pair = (candidate["uri"], relay["uri"])
            (self.pairs.add if result.ok else self.pairs.discard)(pair)
        # Only standalone tests update standalone candidate scores.
        if mode != CHAIN:
            candidate["last_ok_at" if result.ok else "last_fail_at"] = result.time
            if result.ok:
                candidate["last_latency"] = result.latency
        log(f"[chain] pinned {mode} {candidate['tag']}" +
            (f" via {relay['tag']}" if relay else "") + f": {'ok' if result.ok else result.reason}")
        return result

    def diagnostic_pairs(self):
        hosts, relays = set(), []
        for relay in self.pool():
            if relay["host"] not in hosts:
                relays.append(relay)
                hosts.add(relay["host"])
        suitable = sorted(self.exits, key=lambda c: (-(c.get("last_ok_at") or 0),
                                                   -(c.get("fallback_score") or 0)))[:2]
        return [(CHAIN, exit_candidate, relay) for relay in relays[:2] for exit_candidate in suitable]

    def startup_jobs(self):
        jobs = [(DIRECT, c, None) for c in self.exits]
        jobs.extend((SUBSCRIPTION, c, None) for c in native_candidate_order(self.relays)[:6])
        return jobs

