import copy
import json
import os
import random
import socket
import subprocess
import time

from .status import log, set_status, status_candidate_fields
from .vless import candidate_is_quarantined, native_candidate_order, primary_candidate, standby_candidate
from .xray_process import XRAY, curl_check, terminate_process


def random_check_delay(args):
    return random.uniform(args.candidate_check_min_interval, args.candidate_check_max_interval)


def candidate_check_enabled(args):
    return (
        args.candidate_check_min_interval > 0
        and args.candidate_check_max_interval > 0
        and args.candidate_check_max_interval >= args.candidate_check_min_interval
    )


def free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_candidate_config(candidate, port):
    outbound = copy.deepcopy(candidate["outbound"])
    outbound["tag"] = "candidate-check"
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "candidate-check-socks",
                "listen": "127.0.0.1",
                "port": port,
                "protocol": "socks",
                "settings": {"udp": False},
            }
        ],
        "outbounds": [
            outbound,
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
    }
    path = f"/tmp/proxy-xray-candidate-check-{port}.json"
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, separators=(",", ":"))
    return path


def check_candidate(candidate, args):
    port = free_local_port()
    config_path = write_candidate_config(candidate, port)
    proc = subprocess.Popen([XRAY, "-c", config_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(args.candidate_check_start_delay)
        ok, latency = curl_check(port, args.health_url, args.candidate_check_timeout)
    finally:
        terminate_process(proc)
        try:
            os.unlink(config_path)
        except OSError:
            pass
    return ok, latency


def weighted_choice(candidates, args):
    if not candidates:
        return None
    extra_weight = max(1, int(getattr(args, "candidate_check_extra_weight", 5) or 1))
    weights = [extra_weight if candidate.get("source") == "extra" else 1 for candidate in candidates]
    return random.choices(candidates, weights=weights, k=1)[0]


def choose_candidate_for_check(candidates, args):
    primary = primary_candidate(candidates)
    standby = standby_candidate(candidates, primary=primary, max_age=getattr(args, "standby_max_age", 600))
    if standby:
        return weighted_choice(candidates, args), "weighted-random"

    primary_uri = primary.get("uri") if primary else None
    pool = [
        candidate
        for candidate in native_candidate_order(candidates)
        if candidate.get("uri") != primary_uri and not candidate_is_quarantined(candidate)
    ]
    if not pool:
        return weighted_choice(candidates, args), "weighted-random"
    return weighted_choice(pool[: min(10, len(pool))], args), "standby-search"


def run_random_candidate_check(candidates, args):
    if not candidates:
        return
    candidate, check_mode = choose_candidate_for_check(candidates, args)
    started = time.time()
    log(
        f"checking {check_mode} candidate "
        f"{candidate.get('tag')} ({candidate.get('host')}:{candidate.get('port')})"
    )
    try:
        ok, latency = check_candidate(candidate, args)
    except Exception as exc:
        ok = False
        latency = None
        log(f"candidate check failed to run for {candidate.get('tag')}: {exc}")

    if ok:
        candidate["last_latency"] = round(latency, 6)
        candidate["last_ok_at"] = time.time()
        log(f"candidate ok {candidate.get('tag')}: {latency:.3f}s")
    else:
        candidate["last_fail_at"] = time.time()
        log(f"candidate failed {candidate.get('tag')}")

    set_status(
        **status_candidate_fields(candidates, getattr(args, "standby_max_age", 600)),
        last_candidate_check={
            "time": time.time(),
            "started_at": started,
            "status": "ok" if ok else "failed",
            "tag": candidate.get("tag"),
            "host": candidate.get("host"),
            "port": candidate.get("port"),
            "latency": round(latency, 6) if latency is not None else None,
        },
    )
