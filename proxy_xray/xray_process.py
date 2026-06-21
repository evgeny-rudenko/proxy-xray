import subprocess
import time
import re
import copy

from .status import log, public_candidate, set_status, status_config_fields
from .vless import native_candidate_order
from .xray_config import XRAY_API_PORT, config_fingerprint, make_native_balancer_config, write_config


XCONF = "/tmp/proxy-xray.json"
XRAY = "/usr/local/bin/xray"


def candidate_by_tag(candidates, tag):
    for candidate in candidates:
        if candidate.get("tag") == tag:
            return candidate
    return None


def terminate_process(proc):
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def curl_check(port, url, timeout):
    command = [
        "curl",
        "-o",
        "/dev/null",
        "-sS",
        "--max-time",
        str(timeout),
        "-w",
        "%{time_total}",
        "-x",
        f"socks5h://127.0.0.1:{port}",
        url,
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        return False, float(timeout)
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        return False, elapsed
    try:
        elapsed = float(result.stdout.strip())
    except ValueError:
        pass
    return True, elapsed


def throughput_check(port, url, timeout):
    command = [
        "curl",
        "-f",
        "-o",
        "/dev/null",
        "-sS",
        "--max-time",
        str(timeout),
        "-w",
        "%{speed_download}",
        "-x",
        f"socks5h://127.0.0.1:{port}",
        url,
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        return False, 0.0
    if result.returncode != 0:
        return False, 0.0
    try:
        bytes_per_second = float(result.stdout.strip())
    except ValueError:
        return False, 0.0
    return True, bytes_per_second * 8 / 1000


def parse_balancer_selected_tag(output):
    in_selects = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("- Selects:"):
            in_selects = True
            continue
        if stripped.startswith("- ") and in_selects:
            break
        if in_selects and stripped:
            parts = stripped.split()
            if len(parts) >= 2:
                return parts[1]
    for pattern in (
        r"selected\s*:\s*\"?([A-Za-z0-9_.-]+)",
        r"selecting\s*:\s*\"?([A-Za-z0-9_.-]+)",
        r"selects\s*:\s*\"?([A-Za-z0-9_.-]+)",
        r"outbound\s*:\s*\"?([A-Za-z0-9_.-]+)",
    ):
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def balancer_info(candidates, args, timeout=3, api_port=XRAY_API_PORT, balancer_tag="auto"):
    command = [
        XRAY,
        "api",
        "bi",
        "--server",
        f"127.0.0.1:{api_port}",
        "-t",
        str(timeout),
        balancer_tag,
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        return {"status": "fail", "detail": f"timeout after {timeout}s", "time": time.time()}

    output = result.stdout.strip()
    error = result.stderr.strip()
    selected_tag = parse_balancer_selected_tag(output)
    selected = candidate_by_tag(candidates, selected_tag) if selected_tag else None
    fallback = candidates[0] if candidates else None
    status = "ok" if result.returncode == 0 else "fail"
    detail = "Xray balancer API"
    if result.returncode != 0:
        detail = error.splitlines()[-1] if error else f"xray api exited with code {result.returncode}"
    elif selected_tag:
        detail = f"selected {selected_tag}"
    else:
        detail = "selection is handled by Xray; selected tag not exposed in API output"

    return {
        "status": status,
        "detail": detail,
        "time": time.time(),
        "balancer": balancer_tag,
        "strategy": args.balancer_strategy,
        "selected_tag": selected_tag,
        "selected": public_candidate(selected) if selected else None,
        "fallback": public_candidate(fallback) if fallback else None,
        "raw": output[-4000:] if output else "",
    }


def start_native_xray(
    candidates,
    args,
    rules,
    inject,
    config_path=XCONF,
    socks_port=1080,
    http_port=8123,
    dns_port=5353,
    api_port=XRAY_API_PORT,
    inbound_vless_port=None,
    label="native balancer",
    update_status_config=True,
):
    candidates = native_candidate_order(candidates)
    config_candidates = copy.deepcopy(candidates)
    config = make_native_balancer_config(
        config_candidates,
        args.dns,
        rules,
        inject,
        args,
        socks_port=socks_port,
        http_port=http_port,
        dns_port=dns_port,
        api_port=api_port,
        inbound_vless_port=inbound_vless_port,
    )
    write_config(config_path, config)
    if update_status_config:
        set_status(**status_config_fields(config))
    fallback = config_candidates[0]
    log(
        f"{label} selected {len(config_candidates)} candidates; "
        f"fallback: {fallback['tag']} ({fallback['host']}:{fallback['port']}); "
        f"socks={socks_port} http={http_port}"
    )
    stdout = None if args.debug else subprocess.DEVNULL
    stderr = None if args.debug else subprocess.DEVNULL
    return subprocess.Popen([XRAY, "-c", config_path], stdout=stdout, stderr=stderr), candidates, config_fingerprint(config)
