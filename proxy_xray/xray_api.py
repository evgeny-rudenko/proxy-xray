import re
import subprocess
import time

from .status import public_candidate


XRAY = "/usr/local/bin/xray"


def candidate_by_tag(candidates, tag):
    for candidate in candidates:
        if candidate.get("tag") == tag:
            return candidate
    return None


def parse_balancer_select_tags(output):
    tags = []
    in_selects = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("- Selects:"):
            in_selects = True
            continue
        if stripped.startswith("- ") and in_selects:
            break
        if not in_selects or not stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            tags.append(parts[1])
    return tags


def parse_balancer_selected_tag(output):
    tags = parse_balancer_select_tags(output)
    if tags:
        return tags[0]
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


def balancer_snapshot(candidates, args, api_port, timeout=None, balancer_tag="auto"):
    timeout = timeout or min(3, max(1, int(getattr(args, "diagnostics_timeout", 3) or 3)))
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
    started = time.monotonic()
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        return {
            "status": "fail",
            "detail": f"timeout after {timeout}s",
            "time": time.time(),
            "api_port": api_port,
            "balancer": balancer_tag,
            "strategy": args.balancer_strategy,
            "selected_tag": None,
            "selected": None,
            "selects": [],
            "fallback": public_candidate(candidates[0]) if candidates else None,
            "duration": round(time.monotonic() - started, 3),
            "raw": "",
        }

    output = result.stdout.strip()
    error = result.stderr.strip()
    selected_tag = parse_balancer_selected_tag(output)
    select_tags = parse_balancer_select_tags(output)
    selected = candidate_by_tag(candidates, selected_tag) if selected_tag else None
    fallback = candidates[0] if candidates else None
    status = "ok" if result.returncode == 0 else "fail"
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
        "api_port": api_port,
        "balancer": balancer_tag,
        "strategy": args.balancer_strategy,
        "selected_tag": selected_tag,
        "selected": public_candidate(selected) if selected else None,
        "selects": [
            public_candidate(candidate_by_tag(candidates, tag))
            for tag in select_tags
            if candidate_by_tag(candidates, tag)
        ],
        "fallback": public_candidate(fallback) if fallback else None,
        "duration": round(time.monotonic() - started, 3),
        "raw": output[-4000:] if output else "",
    }
