"""Sequential, pinned probes; a failed chain never blames its relay."""
import copy
import os
import subprocess
import tempfile
import time
import socket
from contextlib import contextmanager
from dataclasses import dataclass

from .health import curl_probe
from .xray_process import XRAY, terminate_process


@dataclass(frozen=True)
class Probe:
    ok: bool
    latency: float = None
    reason: str = ""
    time: float = 0


def health_at(port, args):
    for url in (args.health_url, args.health_backup_url):
        item = curl_probe("route", url, args.health_timeout, proxy=f"socks5h://127.0.0.1:{port}",
                          expected_codes={"200", "204"})
        if item["status"] == "ok":
            return Probe(True, item["latency"], "end-to-end HTTP health", time.time())
    return Probe(False, reason="both health endpoints failed", time=time.time())


@contextmanager
def pinned_session(exit_candidate, args, relay=None):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    outbound = copy.deepcopy(exit_candidate["outbound"])
    outbound["tag"] = "pinned-exit"
    outbounds = [outbound]
    if relay is not None:
        upstream = copy.deepcopy(relay["outbound"])
        upstream["tag"] = "pinned-relay"
        outbound.setdefault("streamSettings", {}).setdefault("sockopt", {})["dialerProxy"] = "pinned-relay"
        outbounds.append(upstream)
    config = {"log": {"loglevel": "none"}, "inbounds": [
        {"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"udp": False}}
    ], "outbounds": outbounds, "dns": {"servers": ["127.0.0.1"], "queryStrategy": "UseIPv4"}}
    from .util import save_json_file
    fd, path = tempfile.mkstemp(prefix="chain-probe-", suffix=".json")
    os.close(fd)
    proc = None
    try:
        save_json_file(path, config)
        proc = subprocess.Popen([XRAY, "run", "-c", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(max(0.2, args.candidate_check_start_delay))
        yield port, proc
    finally:
        terminate_process(proc)
        os.unlink(path)


def pinned_probe(exit_candidate, args, relay=None, repeat=1):
    with pinned_session(exit_candidate, args, relay) as (port, proc):
        result = Probe(False, reason="probe did not run", time=time.time())
        for _ in range(repeat):
            if proc.poll() is not None:
                return Probe(False, reason="probe Xray failed to start", time=time.time())
            result = health_at(port, args)
            if not result.ok:
                break
        return result

