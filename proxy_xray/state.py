import json
import os
import time

from .status import log
from .util import save_json_file
from .vless import parse_vless_uri


SCHEMA_VERSION = 2
MAX_RECENT_CHECKS = 50


def empty_state():
    return {"schema_version": SCHEMA_VERSION, "candidates": {}, "last_selected_uri": None}


def number_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_recent_checks(value):
    if not isinstance(value, list):
        return []
    checks = []
    for item in value[-MAX_RECENT_CHECKS:]:
        if not isinstance(item, dict):
            continue
        checked_at = number_or_none(item.get("time"))
        if not checked_at:
            continue
        checks.append(
            {
                "time": checked_at,
                "kind": str(item.get("kind") or "health"),
                "ok": bool(item.get("ok")),
                "latency": number_or_none(item.get("latency")),
                "throughput_kbps": number_or_none(item.get("throughput_kbps")),
            }
        )
    return checks


def ewma(values, alpha=0.35):
    result = None
    for value in values:
        if value is None:
            continue
        result = float(value) if result is None else alpha * float(value) + (1 - alpha) * result
    return round(result, 6) if result is not None else None


def quality_from_checks(recent_checks, now=None):
    now = now or time.time()
    checks = len(recent_checks)
    successes = sum(1 for item in recent_checks if item.get("ok"))
    failures = checks - successes
    consecutive_failures = 0
    for item in reversed(recent_checks):
        if item.get("ok"):
            break
        consecutive_failures += 1
    latency_values = [item.get("latency") for item in recent_checks if item.get("ok")]
    throughput_values = [item.get("throughput_kbps") for item in recent_checks if item.get("throughput_kbps")]
    last_24h_failures = sum(
        1
        for item in recent_checks
        if not item.get("ok") and now - float(item.get("time") or 0) <= 24 * 3600
    )
    return {
        "checks": checks,
        "successes": successes,
        "failures": failures,
        "success_rate": round(successes / checks, 4) if checks else None,
        "consecutive_failures": consecutive_failures,
        "last_24h_failures": last_24h_failures,
        "latency_ewma": ewma(latency_values),
        "throughput_ewma": ewma(throughput_values),
        "last_check_at": recent_checks[-1]["time"] if recent_checks else None,
        "last_success_at": max((item["time"] for item in recent_checks if item.get("ok")), default=None),
        "last_failure_at": max((item["time"] for item in recent_checks if not item.get("ok")), default=None),
    }


def normalize_candidate_record(record):
    if not isinstance(record, dict):
        record = {}
    recent_checks = normalize_recent_checks(record.get("recent_checks"))
    normalized = dict(record)
    normalized["recent_checks"] = recent_checks
    normalized["quality"] = quality_from_checks(recent_checks)
    return normalized


def normalize_state(state):
    if not isinstance(state, dict):
        return empty_state()
    candidates = state.get("candidates")
    if not isinstance(candidates, dict):
        candidates = {}
    normalized = dict(state)
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["candidates"] = {
        uri: normalize_candidate_record(record)
        for uri, record in candidates.items()
        if isinstance(uri, str) and uri
    }
    normalized.setdefault("last_selected_uri", None)
    return normalized


def backup_corrupt_state(path, exc):
    timestamp = int(time.time())
    backup_path = f"{path}.corrupt.{timestamp}"
    try:
        os.replace(path, backup_path)
        log(f"state file is corrupt and was moved to {backup_path}: {exc}")
    except OSError as backup_exc:
        log(f"state file is corrupt and could not be moved aside: {exc}; backup failed: {backup_exc}")


def load_state(path):
    if not path:
        return empty_state()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return normalize_state(json.load(handle))
    except FileNotFoundError:
        return empty_state()
    except json.JSONDecodeError as exc:
        backup_corrupt_state(path, exc)
        return empty_state()
    except OSError as exc:
        log(f"state load failed: {exc}")
        return empty_state()


def copy_saved_candidate_fields(candidate, previous):
    candidate["last_latency"] = previous.get("last_latency")
    candidate["last_throughput_kbps"] = previous.get("last_throughput_kbps")
    candidate["last_ok_at"] = previous.get("last_ok_at")
    candidate["last_fail_at"] = previous.get("last_fail_at")
    candidate["last_xray_selected_at"] = previous.get("last_xray_selected_at")
    candidate["last_xray_selected_slot"] = previous.get("last_xray_selected_slot")
    candidate["quarantine_until"] = previous.get("quarantine_until")
    candidate["quarantine_reason"] = previous.get("quarantine_reason")
    candidate["quality"] = previous.get("quality") if isinstance(previous.get("quality"), dict) else {}
    candidate["recent_checks"] = normalize_recent_checks(previous.get("recent_checks"))
    return candidate


def apply_persisted_state(candidates, state):
    saved = state.get("candidates", {})
    for candidate in candidates:
        previous = saved.get(candidate["uri"])
        if not isinstance(previous, dict):
            continue
        copy_saved_candidate_fields(candidate, previous)
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
        copy_saved_candidate_fields(item, previous)
        candidates.append(item)
        index += 1
    return candidates


def latest_recorded_check_at(recent_checks):
    return max((float(item.get("time") or 0) for item in recent_checks), default=0.0)


def latest_candidate_check(candidate):
    last_ok_at = number_or_none(candidate.get("last_ok_at"))
    last_fail_at = number_or_none(candidate.get("last_fail_at"))
    if not last_ok_at and not last_fail_at:
        return None
    if last_ok_at and (not last_fail_at or last_ok_at >= last_fail_at):
        return {
            "time": last_ok_at,
            "kind": "health",
            "ok": True,
            "latency": number_or_none(candidate.get("last_latency")),
            "throughput_kbps": number_or_none(candidate.get("last_throughput_kbps")),
        }
    return {
        "time": last_fail_at,
        "kind": "health",
        "ok": False,
        "latency": None,
        "throughput_kbps": None,
    }


def append_latest_candidate_check(record, candidate, now=None):
    recent_checks = normalize_recent_checks(record.get("recent_checks"))
    event = latest_candidate_check(candidate)
    if event and event["time"] > latest_recorded_check_at(recent_checks) + 0.000001:
        recent_checks.append(event)
    recent_checks = recent_checks[-MAX_RECENT_CHECKS:]
    record["recent_checks"] = recent_checks
    record["quality"] = quality_from_checks(recent_checks, now=now)
    return record


def candidate_record(candidate, previous=None, now=None):
    previous = normalize_candidate_record(previous or {})
    record = {
        "name": candidate["name"],
        "host": candidate["host"],
        "port": candidate["port"],
        "source": candidate.get("source"),
        "tag": candidate.get("tag"),
        "last_latency": candidate.get("last_latency"),
        "last_throughput_kbps": candidate.get("last_throughput_kbps"),
        "last_ok_at": candidate.get("last_ok_at"),
        "last_fail_at": candidate.get("last_fail_at"),
        "last_xray_selected_at": candidate.get("last_xray_selected_at"),
        "last_xray_selected_slot": candidate.get("last_xray_selected_slot"),
        "quarantine_until": candidate.get("quarantine_until"),
        "quarantine_reason": candidate.get("quarantine_reason"),
        "recent_checks": previous.get("recent_checks", []),
    }
    return append_latest_candidate_check(record, candidate, now=now)


def persist_state(args, candidates, active=None):
    if not args.state_file:
        return
    previous_state = load_state(args.state_file)
    previous_candidates = previous_state.get("candidates", {})
    now = time.time()
    saved = {}
    for candidate in candidates:
        record = candidate_record(candidate, previous_candidates.get(candidate["uri"]), now=now)
        candidate["quality"] = record.get("quality", {})
        candidate["recent_checks"] = record.get("recent_checks", [])
        saved[candidate["uri"]] = record
    if active:
        last_selected_uri = active["uri"]
    else:
        last_selected_uri = None
    state = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now,
        "last_selected_uri": last_selected_uri,
        "last_balancer_fallback_uri": candidates[0]["uri"] if candidates else None,
        "candidates": saved,
    }
    try:
        save_json_file(args.state_file, state)
    except Exception as exc:
        log(f"state save failed: {exc}")
