import time

from .status import log, public_candidate
from .xray_api import balancer_snapshot
from .xray_process import curl_check, start_native_xray, terminate_process, throughput_check


SLOTS = (
    {
        "name": "active-a",
        "config_path": "/tmp/proxy-xray-active-a.json",
        "socks_port": 11080,
        "http_port": 18123,
        "dns_port": 15353,
        "api_port": 11085,
        "vless_port": 11086,
    },
    {
        "name": "active-b",
        "config_path": "/tmp/proxy-xray-active-b.json",
        "socks_port": 12080,
        "http_port": 18223,
        "dns_port": 15354,
        "api_port": 12085,
        "vless_port": 12086,
    },
)


def fresh_slot(template):
    slot = dict(template)
    slot.update(
        {
            "proc": None,
            "candidate": None,
            "candidate_uri": None,
            "candidates": [],
            "candidate_uris": [],
            "fingerprint": None,
            "healthy": False,
            "failures": 0,
            "last_health": None,
            "started_at": None,
        }
    )
    return slot


def slot_alive(slot):
    proc = slot.get("proc")
    return bool(proc and proc.poll() is None)


def stop_slot(slot):
    terminate_process(slot.get("proc"))
    slot["proc"] = None
    slot["healthy"] = False
    slot["candidate"] = None
    slot["candidate_uri"] = None
    slot["candidates"] = []
    slot["candidate_uris"] = []


def normalize_slot_candidates(candidates):
    if not candidates:
        return []
    if isinstance(candidates, dict):
        return [candidates]
    return [candidate for candidate in candidates if candidate]


def slot_candidates(slot):
    return normalize_slot_candidates(slot.get("candidates") or slot.get("candidate"))


def slot_candidate_tags(slot):
    return ", ".join(candidate.get("tag") or "unknown" for candidate in slot_candidates(slot)) or "-"


def candidate_by_uri(candidates, uri):
    if not uri:
        return None
    for candidate in candidates:
        if candidate.get("uri") == uri:
            return candidate
    return None


def refresh_slot_candidate_refs(slots, candidates):
    for slot in slots:
        refreshed = []
        for uri in slot.get("candidate_uris", []):
            candidate = candidate_by_uri(candidates, uri)
            if candidate:
                refreshed.append(candidate)
        if refreshed:
            slot["candidates"] = refreshed
            slot["candidate"] = refreshed[0]
            slot["candidate_uri"] = refreshed[0].get("uri")
            continue
        candidate = candidate_by_uri(candidates, slot.get("candidate_uri"))
        if candidate:
            slot["candidates"] = [candidate]
            slot["candidate"] = candidate


def candidate_label(candidate):
    return candidate.get("tag") or candidate.get("name") or f"{candidate.get('host')}:{candidate.get('port')}"


def start_slot(slot, candidates, args, rules, inject, active=False):
    candidates = normalize_slot_candidates(candidates)
    if not candidates:
        stop_slot(slot)
        return slot
    stop_slot(slot)
    proc, ordered, fingerprint = start_native_xray(
        candidates,
        args,
        rules,
        inject,
        config_path=slot["config_path"],
        socks_port=slot["socks_port"],
        http_port=slot["http_port"],
        dns_port=slot["dns_port"],
        api_port=slot["api_port"],
        inbound_vless_port=slot["vless_port"],
        label=f"{slot['name']} {'active' if active else 'hot standby'}",
        update_status_config=active,
    )
    fallback = ordered[0]
    slot["proc"] = proc
    slot["candidates"] = ordered
    slot["candidate_uris"] = [candidate.get("uri") for candidate in ordered if candidate.get("uri")]
    slot["candidate"] = fallback
    slot["candidate_uri"] = fallback.get("uri")
    slot["fingerprint"] = fingerprint
    slot["healthy"] = False
    slot["failures"] = 0
    slot["last_health"] = None
    slot["started_at"] = time.time()
    return slot


def check_slot(slot, args, record_candidate=True):
    if not slot.get("candidate") or not slot_alive(slot):
        slot["healthy"] = False
        slot["failures"] += 1
        slot["last_health"] = {"time": time.time(), "status": "failed", "latency": None}
        return False, None

    ok, latency = curl_check(slot["socks_port"], args.health_url, args.health_timeout)
    now = time.time()
    slot["healthy"] = ok
    slot["last_health"] = {
        "time": now,
        "status": "ok" if ok else "failed",
        "latency": round(latency, 3) if latency is not None else None,
    }
    if ok:
        slot["failures"] = 0
        if record_candidate:
            selected = record_xray_selected_candidate(slot, args, latency=latency, mark_ok=True)
            if not selected or selected is slot["candidate"]:
                slot["candidate"]["last_ok_at"] = now
                slot["candidate"]["last_latency"] = latency
    else:
        slot["failures"] += 1
        if record_candidate:
            slot["candidate"]["last_fail_at"] = now
    return ok, latency


def check_slot_large_download(slot, args):
    candidate = slot.get("candidate")
    if not candidate or not slot_alive(slot):
        return False, 0.0
    ok, throughput_kbps = throughput_check(slot["socks_port"], args.throughput_url, args.throughput_max_time)
    now = time.time()
    if ok and throughput_kbps >= args.throughput_min_kbps:
        selected = record_xray_selected_candidate(slot, args, throughput_kbps=throughput_kbps, mark_ok=True)
        if not selected or selected is candidate:
            candidate["last_ok_at"] = now
            candidate["last_throughput_kbps"] = round(throughput_kbps)
        log(
            f"hot standby large download ok {candidate_label(candidate)}: "
            f"{throughput_kbps:.0f} kbps"
        )
        return True, throughput_kbps
    candidate["last_fail_at"] = now
    log(
        f"hot standby large download failed {candidate_label(candidate)}: "
        f"{throughput_kbps:.0f} kbps < {args.throughput_min_kbps:.0f} kbps"
    )
    return False, throughput_kbps


def slot_public_status(slot):
    candidate = slot.get("candidate")
    candidates = slot_candidates(slot)
    return {
        "name": slot.get("name"),
        "running": slot_alive(slot),
        "healthy": slot.get("healthy"),
        "failures": slot.get("failures", 0),
        "ports": {
            "socks": slot.get("socks_port"),
            "http": slot.get("http_port"),
            "vless": slot.get("vless_port"),
            "api": slot.get("api_port"),
        },
        "candidate": public_candidate(candidate) if candidate else None,
        "candidates": [public_candidate(candidate) for candidate in candidates],
        "pool_size": len(candidates),
        "last_health": slot.get("last_health"),
        "started_at": slot.get("started_at"),
    }


def xray_api_status_for_slot(slot, args):
    candidate = slot.get("candidate")
    selected = public_candidate(candidate) if candidate else None
    candidates = slot_candidates(slot)
    fallback_status = {
        "status": "ok" if slot_alive(slot) else "fail",
        "detail": "xray api unavailable because slot is not running" if not slot_alive(slot) else "xray api not checked",
        "time": time.time(),
        "slot": slot.get("name"),
        "api_port": slot.get("api_port"),
        "balancer": "auto",
        "strategy": args.balancer_strategy,
        "selected_tag": selected.get("tag") if selected else None,
        "selected": selected,
        "fallback": selected,
        "selects": [],
        "pool": [public_candidate(candidate) for candidate in candidates],
        "pool_size": len(candidates),
        "raw": "",
    }
    if not slot_alive(slot):
        return fallback_status
    try:
        snapshot = balancer_snapshot(candidates, args, api_port=slot["api_port"])
    except Exception as exc:
        fallback_status["status"] = "fail"
        fallback_status["detail"] = f"xray api failed: {exc}"
        return fallback_status
    snapshot["slot"] = slot.get("name")
    snapshot["pool"] = [public_candidate(candidate) for candidate in candidates]
    snapshot["pool_size"] = len(candidates)
    if not snapshot.get("selected"):
        snapshot["selected"] = snapshot.get("fallback")
        snapshot["selected_tag"] = (snapshot.get("fallback") or {}).get("tag")
    return snapshot


def active_path_for_slot(slot, args):
    return xray_api_status_for_slot(slot, args)


def candidate_by_tag(candidates, tag):
    for candidate in candidates or []:
        if candidate.get("tag") == tag:
            return candidate
    return None


def record_xray_selected_candidate(slot, args, snapshot=None, latency=None, throughput_kbps=None, mark_ok=False):
    if not slot_alive(slot):
        return None
    snapshot = snapshot or xray_api_status_for_slot(slot, args)
    selected_tag = snapshot.get("selected_tag")
    candidate = candidate_by_tag(slot_candidates(slot), selected_tag)
    if not candidate:
        return None
    now = time.time()
    candidate["last_xray_selected_at"] = now
    candidate["last_xray_selected_slot"] = slot.get("name")
    if mark_ok:
        candidate["last_ok_at"] = now
        if latency is not None:
            candidate["last_latency"] = latency
    if throughput_kbps is not None:
        candidate["last_throughput_kbps"] = round(throughput_kbps)
    return candidate
