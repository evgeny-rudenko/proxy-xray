import subprocess
import unittest
from unittest.mock import Mock, patch

from test_chain import args, candidate
import test_supervisor
from proxy_xray.chain_config import SLOTS
from proxy_xray.chain_process import ChainCore
from proxy_xray.health import curl_probe
from proxy_xray.route_health import rescue_direct
from proxy_xray.route_probe import Probe, health_at
from proxy_xray.routes import DIRECT, CHAIN, SUBSCRIPTION_ONLY


def transport(index, network):
    item = candidate(index=index)
    item["outbound"]["streamSettings"]["network"] = network
    return item


class RouteHealthTest(unittest.TestCase):
    def supervisor(self):
        supervisor = test_supervisor.SupervisorTest().supervisor(DIRECT)
        grpc, xhttp = transport(3, "grpc"), transport(2, "xhttp")
        supervisor.core.mapping = {"exit-direct-" + c["tag"]: c for c in (grpc, xhttp)}
        supervisor.core.selected.return_value = ("exit-direct-proxy-extra-3", grpc, True)
        supervisor.core.override_transport.return_value = True
        return supervisor

    def test_second_failure_recovers_using_two_checks_on_working_core(self):
        supervisor = self.supervisor()
        with patch("proxy_xray.supervisor.health_at", side_effect=[
            Probe(False), Probe(True, .1), Probe(True, .2), Probe(True), Probe(True)
        ]) as probe:
            supervisor.check_routes()
        self.assertEqual(DIRECT, supervisor.policy.mode)
        self.assertEqual(0, supervisor.policy.failures)
        supervisor.core.override.assert_not_called()
        supervisor.core.override_transport.assert_called_once_with(DIRECT, "exit-direct-proxy-extra-2")
        self.assertEqual(3, sum(c.args[0] == SLOTS[0]["probe_ports"][DIRECT] for c in probe.call_args_list))

    def test_first_failure_does_not_force_transport(self):
        supervisor = self.supervisor()
        supervisor.policy.failures = 0
        with patch("proxy_xray.supervisor.health_at", return_value=Probe(False)):
            supervisor.check_routes()
        supervisor.core.override_transport.assert_not_called()
        self.assertEqual(DIRECT, supervisor.policy.mode)
        self.assertEqual(1, supervisor.policy.failures)

    def test_failed_confirmation_restores_native_and_switches_to_chain(self):
        supervisor = self.supervisor()
        with patch("proxy_xray.supervisor.health_at", side_effect=[
            Probe(False), Probe(True), Probe(False), Probe(True), Probe(True)
        ]), patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            supervisor.check_routes()
        self.assertEqual(CHAIN, supervisor.policy.mode)
        self.assertEqual([(DIRECT, "exit-direct-proxy-extra-2"), (DIRECT, None)],
                         [c.args for c in supervisor.core.override_transport.call_args_list])

    def test_all_alternatives_fail_restore_previous_pin_not_relay(self):
        supervisor = self.supervisor()
        supervisor.core.transport_overrides = {DIRECT: "exit-direct-proxy-extra-3"}
        with patch("proxy_xray.supervisor.health_at", side_effect=[Probe(False), Probe(False), Probe(True), Probe(True)]), patch(
            "proxy_xray.supervisor.set_dns_proxy_ready"
        ):
            supervisor.check_routes()
        supervisor.core.override_transport.assert_called_with(DIRECT, "exit-direct-proxy-extra-3")
        self.assertEqual(CHAIN, supervisor.policy.mode)

    def test_failed_override_is_not_accepted_without_probe(self):
        supervisor = self.supervisor()
        supervisor.core.override_transport.return_value = False
        probe = Mock()
        self.assertIsNone(rescue_direct(supervisor.core, supervisor.args, "exit-direct-proxy-extra-3", probe))
        probe.assert_not_called()

    def test_candidates_are_bounded_and_exclude_tcp_and_subscription(self):
        supervisor = self.supervisor()
        extra = transport(0, "tcp")
        relay = candidate("subscription")
        supervisor.core.mapping.update({"exit-direct-proxy-extra-0": extra, "relay-proxy-sub-0-hop": relay})
        probe = Mock(return_value=Probe(False))
        self.assertIsNone(rescue_direct(supervisor.core, supervisor.args, "UNKNOWN", probe))
        self.assertEqual(2, probe.call_count)
        attempted = [c.args[1] for c in supervisor.core.override_transport.call_args_list[:-1]]
        self.assertEqual(["exit-direct-proxy-extra-3", "exit-direct-proxy-extra-2"], attempted)

    def test_subscription_policy_never_runs_direct_rescue(self):
        supervisor = self.supervisor()
        supervisor.policy.commit(CHAIN, "test", now=0)
        supervisor.policy.route_policy = SUBSCRIPTION_ONLY
        with patch("proxy_xray.supervisor.health_at", return_value=Probe(True)):
            supervisor.check_routes()
        supervisor.core.override_transport.assert_not_called()

    def test_curl_exit_and_http_status_are_preserved(self):
        with patch("proxy_xray.health.subprocess.run", return_value=subprocess.CompletedProcess(
            [], 28, "000 5.001", "curl: (28) Operation timed out"
        )):
            result = curl_probe("test", "https://example.test", 5)
        self.assertEqual(28, result["curl_exit"])
        self.assertEqual("000", result["http_code"])
        self.assertEqual(5.001, result["latency"])

    def test_backup_success_retains_primary_error_without_secrets(self):
        error = {"status": "fail", "curl_exit": 28, "http_code": "000", "latency": 5,
                 "detail": "https://user:secret@example.test/path?token=secret 11111111-1111-4111-8111-111111111111"}
        with patch("proxy_xray.route_probe.curl_probe", side_effect=[error, {"status": "ok", "latency": .1}]), patch(
            "proxy_xray.route_probe.log"
        ) as log:
            result = health_at(12345, args(), context="synthetic")
        self.assertTrue(result.ok)
        self.assertEqual(28, result.attempts[0]["curl_exit"])
        self.assertNotIn("secret", str(result.attempts) + str(log.call_args_list))
        self.assertNotIn("11111111-1111", str(result.attempts) + str(log.call_args_list))

    def test_both_failures_are_published_with_reason(self):
        with patch("proxy_xray.route_probe.curl_probe", side_effect=[
            {"status": "fail", "curl_exit": 6, "http_code": "000", "detail": "resolve failed"},
            {"status": "fail", "curl_exit": 0, "http_code": "503", "detail": "HTTP 503"}
        ]):
            result = health_at(12345, args())
        self.assertFalse(result.ok)
        self.assertIn("primary http=000 curl=6", result.reason)
        self.assertIn("backup http=503 curl=0", result.reason)

    def test_transport_api_validates_target_and_removes_override(self):
        core = ChainCore(args(), SLOTS[0], [transport(3, "grpc")], [])
        core.api = Mock(return_value=subprocess.CompletedProcess([], 0))
        self.assertTrue(core.override_transport(DIRECT, "exit-direct-proxy-extra-3"))
        self.assertEqual("exit-direct-proxy-extra-3", core.transport_overrides[DIRECT])
        self.assertFalse(core.override_transport(CHAIN, "exit-direct-proxy-extra-3"))
        self.assertFalse(core.override_transport(DIRECT, "relay-proxy-sub-0-hop"))
        self.assertTrue(core.override_transport(DIRECT))
        core.api.assert_called_with("bo", "-b", "amsterdam-direct", "-r")
        self.assertEqual({}, core.transport_overrides)

    def test_staged_rebuild_retains_pin_before_route_verification(self):
        supervisor = self.supervisor()
        supervisor.core.config = {"old": True}
        supervisor.core.transport_overrides = {DIRECT: "exit-direct-proxy-extra-2"}
        supervisor.inventory.direct = set()
        supervisor.inventory.pairs = set()
        supervisor.switches = {"dns": Mock()}
        # Avoid retirement in this synthetic fixture.
        supervisor.inventory.candidates = list(supervisor.core.mapping.values())
        staged = Mock()
        staged.slot = SLOTS[1]
        staged.mapping = dict(supervisor.core.mapping)
        staged.selected.return_value = ("exit-direct-proxy-extra-2", transport(2, "xhttp"), True)
        def check(*a, **kw):
            staged.override_transport.assert_called_once_with(DIRECT, "exit-direct-proxy-extra-2")
            return Probe(True)
        with patch("proxy_xray.supervisor.make_chain_config", return_value=({}, {})), patch(
            "proxy_xray.supervisor.ChainCore", return_value=staged
        ), patch("proxy_xray.supervisor.health_at", side_effect=check), patch(
            "proxy_xray.supervisor.set_switch_targets"
        ), patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            self.assertTrue(supervisor.rebuild())
        self.assertIs(staged, supervisor.core)

    def test_reused_tag_does_not_pin_a_different_transport_on_rebuild(self):
        supervisor = self.supervisor()
        supervisor.core.config = {"old": True}
        supervisor.core.transport_overrides = {DIRECT: "exit-direct-proxy-extra-2"}
        supervisor.inventory.direct = set()
        supervisor.inventory.pairs = set()
        supervisor.inventory.candidates = list(supervisor.core.mapping.values())
        supervisor.switches = {"dns": Mock()}
        replacement = transport(2, "xhttp")
        replacement["uri"] = "synthetic-replacement"
        staged = Mock()
        staged.slot = SLOTS[1]
        staged.mapping = {"exit-direct-proxy-extra-2": replacement}
        staged.selected.return_value = ("exit-direct-proxy-extra-2", replacement, True)
        with patch("proxy_xray.supervisor.make_chain_config", return_value=({}, {})), patch(
            "proxy_xray.supervisor.ChainCore", return_value=staged
        ), patch("proxy_xray.supervisor.health_at", return_value=Probe(True)), patch(
            "proxy_xray.supervisor.set_switch_targets"
        ), patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            self.assertTrue(supervisor.rebuild())
        staged.override_transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
