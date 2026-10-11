#!/usr/bin/env python3
"""Finite local soak sampler; reads status only, never performs extra provider probes."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from proxy_xray.util import save_json_file

parser = argparse.ArgumentParser()
parser.add_argument("--seconds", type=int, default=10800)
parser.add_argument("--interval", type=int, default=20)
parser.add_argument("--output")
args = parser.parse_args()
if args.seconds <= 0 or args.interval <= 0:
    parser.error("duration and interval must be positive")

started = time.monotonic()
modes, failed_checks, samples = Counter(), Counter(), []
unreadable, cores_down = 0, 0
while True:
    try:
        with urllib.request.urlopen("http://127.0.0.1:18080/json", timeout=5) as response:
            status = json.load(response)
        mode = status.get("route_mode", "unknown")
        modes[mode] += 1
        cores_down += not status.get("xray_running", False)
        failures = [key for key, value in (status.get("health_checks") or {}).items()
                    if value.get("status") == "fail"]
        failed_checks.update(failures)
        samples.append({"time": time.time(), "mode": mode, "xray_running": status.get("xray_running"),
                        "failed_checks": failures, "relay_count": len(status.get("relay_pool") or [])})
    except (OSError, ValueError):
        unreadable += 1
    remaining = args.seconds - (time.monotonic() - started)
    if remaining <= 0:
        break
    time.sleep(min(args.interval, remaining))
report = {"seconds": round(time.monotonic() - started), "modes": dict(modes),
          "failed_checks": dict(failed_checks), "unreadable_samples": unreadable,
          "core_down_samples": cores_down, "samples": samples}
if args.output:
    save_json_file(args.output, report)
print(json.dumps({key: value for key, value in report.items() if key != "samples"}))
