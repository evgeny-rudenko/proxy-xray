"""Start, validate and control one Xray core, with a temporary staging slot."""
import json
import os
import subprocess
import time

from .chain_config import BALANCERS, make_chain_config
from .routes import DIRECT, ROUTE_TAGS, UNAVAILABLE
from .util import save_json_file
from .xray_api import parse_balancer_selected_tag
from .xray_process import XRAY, terminate_process


class ChainCore:
    def __init__(self, args, slot, exits, relays, direct=(), pairs=()):
        self.slot = dict(slot)
        self.args = args
        self.config, self.mapping = make_chain_config(exits, relays, args, slot, direct, pairs)
        self.path = f"/tmp/amsterdam-core-{slot['api_port']}.json"
        self.proc = None
        self.transport_overrides = {}

    def api(self, command, *arguments):
        try:
            return subprocess.run([XRAY, "api", command, "--server", f"127.0.0.1:{self.slot['api_port']}",
                                   "-t", "2", *arguments], capture_output=True, text=True, timeout=4)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess([], 1, "", "API timeout")

    def start(self):
        save_json_file(self.path, self.config)
        result = subprocess.run([XRAY, "run", "-test", "-c", self.path],
                                capture_output=True, timeout=10)
        if result.returncode:
            raise RuntimeError("generated chain configuration did not pass Xray validation")
        self.proc = subprocess.Popen([XRAY, "run", "-c", self.path],
                                     stdout=None if self.args.debug else subprocess.DEVNULL,
                                     stderr=None if self.args.debug else subprocess.DEVNULL)
        deadline = time.monotonic() + 8
        while self.alive() and time.monotonic() < deadline:
            if self.override(UNAVAILABLE):
                return
            time.sleep(0.2)
        self.stop()
        raise RuntimeError("chain core API did not become ready")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def override(self, mode):
        return self.api("bo", "-b", "egress", ROUTE_TAGS[mode]).returncode == 0

    def override_transport(self, mode, tag=None):
        if mode != DIRECT or (tag is not None and tag not in self.mapping):
            return False
        if tag is not None:
            candidate = self.mapping[tag]
            if candidate.get("source") != "extra" or not tag.startswith("exit-direct-"):
                return False
        arguments = (tag,) if tag is not None else ("-r",)
        if self.api("bo", "-b", BALANCERS[mode], *arguments).returncode:
            return False
        if tag is None:
            self.transport_overrides.pop(mode, None)
        else:
            self.transport_overrides[mode] = tag
        return True

    def selected(self, balancer):
        response = self.api("bi", balancer)
        tag = parse_balancer_selected_tag(response.stdout) if response.returncode == 0 else None
        return tag, self.mapping.get(tag), response.returncode == 0

    def stop(self):
        terminate_process(self.proc)
        self.proc = None
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

