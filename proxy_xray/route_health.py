"""Balanced-route diagnostics and bounded recovery inside the direct exit pool."""
from dataclasses import replace

from .chain_config import BALANCERS
from .routes import DIRECT
from .status import log


def selection(core, mode):
    tag, candidate, ok = core.selected(BALANCERS[mode])
    stream = candidate.get("outbound", {}).get("streamSettings", {}) if candidate else {}
    return {"tag": tag or "UNKNOWN", "network": stream.get("network", "UNKNOWN"),
            "security": stream.get("security", "UNKNOWN"), "api_ok": ok}


def probe_route(core, args, mode, probe, context="periodic"):
    before = selection(core, mode)
    result = probe(core.slot["probe_ports"][mode], args,
                   context=f"{context}:{mode}:{before['tag']}:{before['network']}/{before['security']}")
    after = selection(core, mode)
    result = replace(result, selected_before=before, selected_after=after)
    if not result.ok or any(a["status"] != "ok" for a in result.attempts) or context != "periodic":
        log(f"[chain] route check context={context} route={mode} status={'ok' if result.ok else 'fail'} "
            f"before={before['tag']}:{before['network']}/{before['security']} "
            f"after={after['tag']}:{after['network']}/{after['security']} "
            f"latency_s={result.latency} reason={result.reason}")
    return result


def rescue_direct(core, args, failed_tag, probe):
    previous = core.transport_overrides.get(DIRECT)
    alternatives = [(tag, candidate) for tag, candidate in core.mapping.items()
                    if tag.startswith("exit-direct-") and tag != failed_tag
                    and candidate.get("source") == "extra"
                    and candidate["outbound"].get("streamSettings", {}).get("network") in ("grpc", "xhttp")]
    alternatives.sort(key=lambda item: item[1]["outbound"]["streamSettings"]["network"] != "grpc")
    retained = False
    try:
        for tag, _ in alternatives[:2]:
            log(f"[chain] direct transport retry target={tag}; verifying on working core")
            if not core.override_transport(DIRECT, tag):
                log(f"[chain] direct transport override failed target={tag}")
                continue
            for _ in range(2):
                result = probe(DIRECT, context="direct-alternative")
                if not result.ok:
                    break
            else:
                retained = True
                log(f"[chain] direct transport recovered target={tag}; retaining verified override, no route switch")
                return result
    finally:
        if alternatives and not retained:
            if not core.override_transport(DIRECT, previous):
                log("[chain] direct transport restore failed; route failover required")
            else:
                log(f"[chain] direct alternatives failed; restored target={previous or 'leastPing'}")
    return None
