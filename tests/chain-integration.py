#!/usr/bin/env python3
"""Deterministic real-Xray tests. Run in a Docker container with --network none."""
import copy
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from test_chain import candidate, args
from proxy_xray.chain_config import BALANCERS, SLOTS, chain_relay_balancer, relay_tag
from proxy_xray.chain_process import ChainCore
from proxy_xray.routes import DIRECT, CHAIN, SUBSCRIPTION, UNAVAILABLE
from proxy_xray.routes import SUBSCRIPTION_ONLY
from proxy_xray.route_control import apply_route_policy
from proxy_xray.route_inventory import RouteInventory
from proxy_xray.supervisor import Supervisor
from proxy_xray.route_probe import health_at, pinned_probe
from proxy_xray.util import save_json_file
from proxy_xray.xray_process import XRAY, terminate_process

UUID = "11111111-1111-4111-8111-111111111111"


class Canary(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_):
        pass


class ChainIntegration(unittest.TestCase):
    def start_proxy(self, port, block_exit=False, network="tcp"):
        config = {"log": {"loglevel": "none"}, "inbounds": [{
            "listen": "127.0.0.1", "port": port, "protocol": "vless",
            "settings": {"clients": [{"id": UUID}], "decryption": "none"},
            "streamSettings": {"network": network, "security": "none"}
        }], "outbounds": [{"tag": "direct", "protocol": "freedom", "settings": {"finalRules": [{"action": "allow"}]}}]}
        if network == "grpc":
            config["inbounds"][0]["streamSettings"]["grpcSettings"] = {"serviceName": "synthetic-rescue"}
        if block_exit:
            config["outbounds"].append({"tag": "block", "protocol": "blackhole"})
            config["routing"] = {"rules": [{"type": "field", "port": "23001-23002", "outboundTag": "block"}]}
        path = os.path.join(self.tmp.name, f"server-{port}.json")
        save_json_file(path, config)
        proc = subprocess.Popen([XRAY, "run", "-c", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(proc)
        return proc

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.children = []
        self.canary = ThreadingHTTPServer(("127.0.0.1", 0), Canary)
        threading.Thread(target=self.canary.serve_forever, daemon=True).start()
        self.args = args()
        self.args.health_url = self.args.health_backup_url = f"http://127.0.0.1:{self.canary.server_port}/"
        self.args.health_timeout = 1
        self.args.observatory_probe_interval = "1s"
        self.args.inbound_vless = True
        self.args.inbound_vless_id = UUID
        self.exit_servers = [self.start_proxy(23001), self.start_proxy(23002)]
        self.relay_servers = [self.start_proxy(23003), self.start_proxy(23004)]
        self.exits = [self.node("extra", i, 23001 + i) for i in range(2)]
        self.relays = [self.node("subscription", i, 23003 + i) for i in range(2)]
        time.sleep(0.3)
        self.assertTrue(all(p.poll() is None for p in self.children))
        self.core = ChainCore(self.args, SLOTS[0], self.exits, self.relays,
                              {c["uri"] for c in self.exits},
                              {(e["uri"], r["uri"]) for e in self.exits for r in self.relays})
        self.core.config["dns"]["hosts"] = {"ru-canary.test.ru": "127.0.0.1"}
        next(o for o in self.core.config["outbounds"] if o["tag"] == "direct")["settings"] = {"domainStrategy": "UseIPv4"}
        self.core.start()

    def node(self, source, index, port):
        c = candidate(source, index)
        c["host"] = "127.0.0.1"
        c["port"] = port
        c["outbound"]["settings"]["vnext"][0]["address"] = "127.0.0.1"
        c["outbound"]["settings"]["vnext"][0]["port"] = port
        c["outbound"]["streamSettings"] = {"network": "tcp", "security": "none"}
        return c

    def tearDown(self):
        self.core.stop()
        for proc in self.children:
            terminate_process(proc)
        self.canary.shutdown()
        self.canary.server_close()
        self.tmp.cleanup()

    def request(self, port=None, http=False, host=None):
        command = ["curl", "--noproxy", "", "-sS", "--max-time", "2", "-o", "/dev/null", "-w", "%{http_code}",
                   "-x", f"{'http' if http else 'socks5h'}://127.0.0.1:{port or self.core.slot['socks_port']}"]
        if host:
            command.append(f"http://ru-canary.test.ru:{self.canary.server_port}/")
        else:
            command.append(self.args.health_url)
        result = subprocess.run(command, capture_output=True, text=True, timeout=4)
        self.last_request = result.stderr.strip() or result.stdout
        return result.returncode == 0 and result.stdout == "204"

    def wait_working(self, mode, timeout=10):
        deadline = time.monotonic() + timeout
        self.assertTrue(self.core.override(mode))
        while time.monotonic() < deadline:
            if self.request():
                return
            time.sleep(0.2)
        self.fail("native route did not become healthy: " + mode + " / " + self.last_request)

    def test_all_three_modes_http_socks_and_lan_vless(self):
        client = self.node("extra", 8, self.core.slot["vless_port"])
        for mode in (DIRECT, CHAIN, SUBSCRIPTION):
            self.wait_working(mode)
            self.assertTrue(self.request(self.core.slot["http_port"], http=True), mode)
            self.assertTrue(pinned_probe(client, self.args).ok, "LAN VLESS " + mode)

    def test_operator_subscription_policy_fallback_and_no_direct_rescue(self):
        self.wait_working(DIRECT)
        self.args.state_file = os.path.join(self.tmp.name, "operator-state.json")
        supervisor = Supervisor(self.args)
        supervisor.core = self.core
        supervisor.inventory = RouteInventory(self.args, self.exits + self.relays)
        supervisor.inventory.verified_relays = {c["uri"] for c in self.relays}
        supervisor.inventory.direct = {c["uri"] for c in self.exits}
        supervisor.inventory.pairs = {(e["uri"], r["uri"]) for e in self.exits for r in self.relays}
        supervisor.policy.commit(DIRECT, "test")
        supervisor.save = Mock()
        command = {"id": "integration", "name": "route_policy", "payload": {"policy": SUBSCRIPTION_ONLY}}
        with patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            apply_route_policy(supervisor, command)
            self.assertEqual(CHAIN, supervisor.policy.mode)
            client = self.node("extra", 8, self.core.slot["vless_port"])
            for port, http in ((self.core.slot["socks_port"], False), (self.core.slot["http_port"], True)):
                self.assertTrue(self.request(port, http=http))
            self.assertTrue(pinned_probe(client, self.args).ok)
            self.assertTrue(self.request(self.core.slot["dns_port"]))
            # Break only the chain; direct Amsterdam is still healthy throughout.
            self.assertEqual(0, self.core.api("bo", "-b", "amsterdam-chain", "block").returncode)
            for _ in range(2):
                supervisor.check_routes()
            self.assertEqual(SUBSCRIPTION, supervisor.policy.mode)
            self.assertTrue(self.request(self.core.slot["http_port"], http=True))
            self.assertTrue(self.request())
            self.assertTrue(pinned_probe(client, self.args).ok)
            self.assertTrue(self.request(self.core.slot["dns_port"]))
            self.assertTrue(health_at(self.core.slot["probe_ports"][DIRECT], self.args).ok)
            for proc in self.relay_servers:
                terminate_process(proc)
            for _ in range(2):
                supervisor.check_routes()
            self.assertEqual(UNAVAILABLE, supervisor.policy.mode)
            self.assertFalse(self.request())
            self.assertFalse(self.request(self.core.slot["http_port"], http=True))
            self.assertFalse(pinned_probe(client, self.args).ok)
            self.assertFalse(self.request(self.core.slot["dns_port"]))
            self.assertTrue(self.request(self.core.slot["http_port"], http=True, host="test.ru"))

    def test_direct_failure_chain_still_reaches_amsterdam(self):
        self.assertEqual(0, self.core.api("bo", "-b", BALANCERS[DIRECT], "block").returncode)
        self.assertFalse(health_at(self.core.slot["probe_ports"][DIRECT], self.args).ok)
        self.wait_working(CHAIN)

    def test_direct_transport_override_is_actual_api_selection(self):
        self.wait_working(DIRECT)
        self.assertTrue(self.core.override_transport(DIRECT, "exit-direct-proxy-extra-1"))
        self.assertTrue(self.request())
        selected, item, ok = self.core.selected(BALANCERS[DIRECT])
        self.assertTrue(ok)
        self.assertEqual("exit-direct-proxy-extra-1", selected)
        self.assertEqual("proxy-extra-1", item["tag"])
        self.assertTrue(self.core.override_transport(DIRECT))
        self.assertTrue(self.request())

    def test_failed_direct_transport_is_rescued_by_real_grpc_without_route_switch(self):
        self.core.stop()
        terminate_process(self.exit_servers[1])
        self.exit_servers[1] = self.start_proxy(23002, network="grpc")
        self.exits[1]["outbound"]["streamSettings"] = {
            "network": "grpc", "security": "none", "grpcSettings": {"serviceName": "synthetic-rescue"}}
        self.core = ChainCore(self.args, SLOTS[0], self.exits, self.relays,
                              {c["uri"] for c in self.exits},
                              {(e["uri"], r["uri"]) for e in self.exits for r in self.relays})
        self.core.start()
        self.wait_working(DIRECT)
        self.assertTrue(self.core.override_transport(DIRECT, "exit-direct-proxy-extra-0"))
        terminate_process(self.exit_servers[0])
        self.assertFalse(self.request())
        supervisor = Supervisor(self.args)
        supervisor.core = self.core
        supervisor.inventory = RouteInventory(self.args, self.exits + self.relays)
        supervisor.inventory.verified_relays = {c["uri"] for c in self.relays}
        supervisor.policy.commit(DIRECT, "test")
        supervisor.save = Mock()
        with patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            supervisor.check_routes()
            self.assertEqual(DIRECT, supervisor.policy.mode)
            supervisor.check_routes()
        self.assertEqual(DIRECT, supervisor.policy.mode)
        self.assertEqual(0, supervisor.policy.failures)
        self.assertEqual("exit-direct-proxy-extra-1", self.core.transport_overrides[DIRECT])
        self.assertTrue(self.request())
        self.assertTrue(self.request(self.core.slot["http_port"], http=True))
        self.assertTrue(pinned_probe(self.node("extra", 8, self.core.slot["vless_port"]), self.args).ok)
        self.assertTrue(self.request(self.core.slot["dns_port"]))

    def test_exit_failure_subscription_remains_working(self):
        for proc in self.exit_servers:
            terminate_process(proc)
        self.assertFalse(health_at(self.core.slot["probe_ports"][CHAIN], self.args).ok)
        self.wait_working(SUBSCRIPTION)

    def test_one_transport_failure_keeps_native_balancer_available(self):
        self.wait_working(DIRECT)
        terminate_process(self.exit_servers[0])
        self.wait_working(DIRECT)
        selected, _, _ = self.core.selected(BALANCERS[DIRECT])
        self.assertIn(selected, {"exit-direct-proxy-extra-1"})

    def test_one_relay_failure_keeps_chain_available(self):
        self.wait_working(CHAIN)
        terminate_process(self.relay_servers[0])
        self.wait_working(CHAIN)

    def test_all_foreign_routes_closed_but_ru_still_direct(self):
        self.assertTrue(self.core.override(UNAVAILABLE))
        self.assertFalse(self.request())
        self.assertFalse(self.request(self.core.slot["dns_port"]))
        self.assertTrue(self.request(self.core.slot["http_port"], http=True, host="test.ru"))

    def test_loopback_relay_stage_cannot_reenter_exit(self):
        self.assertEqual(0, self.core.api("bo", "-b", chain_relay_balancer(self.exits[0]), "block").returncode)
        self.assertEqual(0, self.core.api("bo", "-b", "amsterdam-chain", "exit-chain-proxy-extra-0").returncode)
        self.assertTrue(self.core.override(CHAIN))
        self.assertFalse(self.request())
        self.wait_working(DIRECT)

    def test_healthy_internet_relay_without_exit_access_is_excluded_from_chain(self):
        self.core.stop()
        terminate_process(self.relay_servers[0])
        self.relay_servers[0] = self.start_proxy(23003, block_exit=True)
        time.sleep(.2)
        self.assertTrue(pinned_probe(self.relays[0], self.args).ok)
        self.assertFalse(pinned_probe(self.exits[0], self.args, self.relays[0]).ok)
        self.core = ChainCore(self.args, SLOTS[0], self.exits, self.relays,
                              {c["uri"] for c in self.exits},
                              {(e["uri"], self.relays[1]["uri"]) for e in self.exits})
        self.core.start()
        self.assertTrue(self.core.api("bo", "-b", "relays", relay_tag(self.relays[0])).returncode == 0)
        self.wait_working(SUBSCRIPTION)
        self.wait_working(CHAIN)
        for exit_candidate in self.exits:
            _, selected, _ = self.core.selected(chain_relay_balancer(exit_candidate))
            if selected:
                self.assertEqual(self.relays[1]["uri"], selected["uri"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
