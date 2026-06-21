import time

from .status import log
from .util import load_json_file, save_json_file
from .vless import parse_vless_uri


def load_state(path):
    state = load_json_file(path, {"candidates": {}, "last_selected_uri": None})
    if not isinstance(state, dict):
        return {"candidates": {}, "last_selected_uri": None}
    if not isinstance(state.get("candidates"), dict):
        state["candidates"] = {}
    return state


def apply_persisted_state(candidates, state):
    saved = state.get("candidates", {})
    for candidate in candidates:
        previous = saved.get(candidate["uri"])
        if not isinstance(previous, dict):
            continue
        candidate["last_latency"] = previous.get("last_latency")
        candidate["last_throughput_kbps"] = previous.get("last_throughput_kbps")
        candidate["last_ok_at"] = previous.get("last_ok_at")
        candidate["last_fail_at"] = previous.get("last_fail_at")
        candidate["quarantine_until"] = previous.get("quarantine_until")
        candidate["quarantine_reason"] = previous.get("quarantine_reason")
    return candidates


def load_cached_subscription_candidates(state, start_index=200000):
    candidates = []
    saved = state.get("candidates", {})
    index = start_index
    for uri, previous in saved.items():
        if not isinstance(previous, dict):
            continue
        if previous.get("source") != "subscription":
            continue
        item = parse_vless_uri(uri, index)
        if not item:
            continue
        item["last_latency"] = previous.get("last_latency")
        item["last_throughput_kbps"] = previous.get("last_throughput_kbps")
        item["last_ok_at"] = previous.get("last_ok_at")
        item["last_fail_at"] = previous.get("last_fail_at")
        item["quarantine_until"] = previous.get("quarantine_until")
        item["quarantine_reason"] = previous.get("quarantine_reason")
        candidates.append(item)
        index += 1
    return candidates


def persist_state(args, candidates, active=None):
    if not args.state_file:
        return
    saved = {}
    for candidate in candidates:
        saved[candidate["uri"]] = {
            "name": candidate["name"],
            "host": candidate["host"],
            "port": candidate["port"],
            "source": candidate.get("source"),
            "tag": candidate.get("tag"),
            "last_latency": candidate.get("last_latency"),
            "last_throughput_kbps": candidate.get("last_throughput_kbps"),
            "last_ok_at": candidate.get("last_ok_at"),
            "last_fail_at": candidate.get("last_fail_at"),
            "quarantine_until": candidate.get("quarantine_until"),
            "quarantine_reason": candidate.get("quarantine_reason"),
        }
    if active:
        last_selected_uri = active["uri"]
    else:
        last_selected_uri = None
    state = {
        "updated_at": time.time(),
        "last_selected_uri": last_selected_uri,
        "last_balancer_fallback_uri": candidates[0]["uri"] if candidates else None,
        "candidates": saved,
    }
    try:
        save_json_file(args.state_file, state)
    except Exception as exc:
        log(f"state save failed: {exc}")
