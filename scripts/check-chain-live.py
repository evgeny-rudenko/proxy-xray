#!/usr/bin/env python3
"""Read private runtime inputs inside the gateway; emit only anonymous results."""
import json
import subprocess
import urllib.request

from proxy_xray.main import build_parser
from proxy_xray.route_probe import health_at, pinned_session
from proxy_xray.state import load_state, load_cached_subscription_candidates
from proxy_xray.subscription import load_extra_candidates

args = build_parser().parse_args(["--sub-url", "https://example.test/sub", "--extra-file", "/opt/proxy-xray/vless-extra.txt"])
args.health_timeout = 8
with urllib.request.urlopen("http://127.0.0.1:18080/json", timeout=5) as response:
    status = json.load(response)
state = load_state("/var/lib/proxy-xray/state.json")
exits = load_extra_candidates(args)
relays = load_cached_subscription_candidates(state)
selected = (status.get("exit") or {}).get("transport") or {}
exit_candidate = next(c for c in exits if c["host"] == selected.get("host") and c["port"] == selected.get("port")
                      and c["outbound"]["streamSettings"]["network"] == selected.get("network"))
report = []
def ip_at(port):
    response = subprocess.run(["curl", "-4", "--noproxy", "", "-fsS", "--max-time", "15",
                               "-x", f"socks5h://127.0.0.1:{port}", "https://api.ipify.org"],
                              capture_output=True, timeout=17)
    return response.stdout.strip() if response.returncode == 0 else None
with pinned_session(exit_candidate, args) as (port, _):
    direct = health_at(port, args).ok
    direct_ip = ip_at(port)
for descriptor in status.get("relay_pool", [])[:2]:
    relay = next(c for c in relays if c["host"] == descriptor["host"] and c["port"] == descriptor["port"]
                 and c["name"] == descriptor["name"])
    with pinned_session(relay, args) as (port, _):
        relay_ok = health_at(port, args).ok
    with pinned_session(exit_candidate, args, relay) as (port, _):
        chained = health_at(port, args).ok
        chained_ip = ip_at(port) if chained else None
    report.append({"relay_healthy": relay_ok, "chain_healthy": chained,
                   "same_exit_ipv4": bool(direct_ip and chained_ip and direct_ip == chained_ip)})
compatible = sum(row["relay_healthy"] and row["chain_healthy"] and row["same_exit_ipv4"] for row in report)
print(json.dumps({"direct_healthy": direct, "exit_ipv4_measured": bool(direct_ip),
                  "compatible_chain_count": compatible, "relay_paths": report}))
# A healthy relay may legitimately fail its Amsterdam pair. Any successful chain must retain the exit IP.
raise SystemExit(not (direct and direct_ip and compatible > 0) or any(
    row["chain_healthy"] and not row["same_exit_ipv4"] for row in report
))
