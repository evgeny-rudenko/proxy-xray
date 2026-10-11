"""Start, validate and control one Xray core, with a temporary staging slot."""
import json
import os
import subprocess
import time

from .chain_config import make_chain_config
from .routes import ROUTE_TAGS, UNAVAILABLE
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

