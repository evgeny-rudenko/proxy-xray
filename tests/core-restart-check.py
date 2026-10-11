"""Local-container-only fault injection; kill the working core and verify recovery."""
import json
import os
from pathlib import Path
import signal
import time
import urllib.request


def cores():
    result = []
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = path.read_bytes().split(b"\0")
        except (OSError, ProcessLookupError):
            continue
        if command and command[0].endswith(b"/xray") and any(
            part.startswith(b"/tmp/amsterdam-core-") for part in command
        ):
            result.append(int(path.parent.name))
    return result


before = cores()
if len(before) != 1:
    raise SystemExit("Wait for startup/staging to finish: expected one working core")
started = time.monotonic()
os.kill(before[0], signal.SIGKILL)
deadline = started + 65
recovered = False
while time.monotonic() < deadline:
    time.sleep(.5)
    current = cores()
    if len(current) != 1 or current == before:
        continue
    try:
        with urllib.request.urlopen("http://127.0.0.1:18080/json", timeout=3) as response:
            status = json.load(response)
        if status.get("xray_running") and status.get("route_mode") != "unavailable":
            from proxy_xray.main import build_parser
            from proxy_xray.route_probe import health_at
            args = build_parser().parse_args(["--sub-url", "https://example.test/sub", "--extra-file", "synthetic"])
            recovered = health_at(1080, args).ok
            if recovered:
                break
    except OSError:
        continue
print(json.dumps({"recovered": recovered, "working_cores": len(cores()),
                  "seconds": round(time.monotonic() - started, 1)}))
raise SystemExit(not recovered)
