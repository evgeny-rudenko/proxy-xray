import http.client
import json
import os
import tempfile
import threading
import unittest
from collections import deque
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from test_chain import args, candidate
import test_supervisor
from proxy_xray.chain_config import SLOTS
from proxy_xray.dashboard_view import dashboard_v5_fragments, render_dashboard_v5_html
from proxy_xray.route_control import apply_route_policy, queue_recheck
from proxy_xray.route_probe import Probe
from proxy_xray.routes import AUTO, SUBSCRIPTION_ONLY, DIRECT, CHAIN, SUBSCRIPTION, UNAVAILABLE, RoutePolicy
from proxy_xray.state import load_state, persist_route_policy, persist_state
from proxy_xray.status import enqueue_control_command, pop_control_commands, mark_control_command, set_status, status_snapshot
from proxy_xray.status_server import StatusHandler
from proxy_xray.util import save_json_file


class RoutingPolicyTest(unittest.TestCase):
    def setUp(self):
        pop_control_commands()
        set_status(route_policy_command=None, last_control_command=None)

    def tearDown(self):
        pop_control_commands()
        set_status(route_policy_command=None, last_control_command=None)

    def supervisor(self, mode=CHAIN):
        return test_supervisor.SupervisorTest().supervisor(mode)

    def command(self, policy=SUBSCRIPTION_ONLY):
        return enqueue_control_command("route_policy", {"policy": policy})

    def test_subscription_policy_ignores_healthy_direct_at_startup(self):
        policy = RoutePolicy(route_policy=SUBSCRIPTION_ONLY)
        policy.observe(DIRECT, True, now=1)
        self.assertEqual(UNAVAILABLE, policy.choose(1))
        policy.observe(SUBSCRIPTION, True, now=2)
        self.assertEqual(SUBSCRIPTION, policy.choose(2))
        policy.observe(CHAIN, True, now=3)
        self.assertEqual(CHAIN, policy.choose(3))

    def test_subscription_policy_never_returns_direct_after_chain_failure(self):
        policy = RoutePolicy(route_policy=SUBSCRIPTION_ONLY)
        policy.commit(CHAIN, "test", now=0)
        for now in (20, 40):
            policy.observe(DIRECT, True, now=now)
            policy.observe(CHAIN, False, now=now)
            policy.observe(SUBSCRIPTION, True, now=now)
        self.assertEqual(SUBSCRIPTION, policy.choose(40))
        policy.observe(SUBSCRIPTION, False, now=60)
        policy.observe(SUBSCRIPTION, False, now=80)
        policy.commit(SUBSCRIPTION, "failed", now=60)
        policy.failures = 2
        self.assertEqual(UNAVAILABLE, policy.choose(80))

    def test_chain_return_retains_success_and_hold_requirements(self):
        policy = RoutePolicy(route_policy=SUBSCRIPTION_ONLY)
        policy.commit(SUBSCRIPTION, "emergency", now=0)
        for now in (20, 40, 60):
            policy.observe(CHAIN, True, now=now)
        self.assertEqual(SUBSCRIPTION, policy.choose(60))
        for now in range(80, 181, 20):
            policy.observe(CHAIN, True, now=now)
        self.assertEqual(CHAIN, policy.choose(180))

    def test_policy_file_survives_regular_history_save(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = args()
            settings.state_file = os.path.join(directory, "state.json")
            save_json_file(settings.state_file, {"route_history": {"test": []}, "route_mode": DIRECT})
            persist_route_policy(settings, SUBSCRIPTION_ONLY)
            persist_state(settings, [])
            saved = load_state(settings.state_file)
            self.assertEqual(SUBSCRIPTION_ONLY, saved["route_policy"])
            self.assertEqual({"test": []}, saved["route_history"])

    def test_invalid_and_corrupt_state_default_auto(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            self.assertEqual(AUTO, load_state(path)["route_policy"])
            save_json_file(path, {"route_policy": "unknown"})
            self.assertEqual(AUTO, load_state(path)["route_policy"])
            with open(path, "w") as handle:
                handle.write("{broken")
            self.assertEqual(AUTO, load_state(path)["route_policy"])

    def test_latest_queued_choice_wins_and_duplicates_coalesce(self):
        first = self.command()
        self.assertEqual(first["id"], self.command()["id"])
        newest = self.command(AUTO)
        self.assertNotEqual(first["id"], newest["id"])
        self.assertEqual([newest], pop_control_commands())
        mark_control_command(first, "done")
        self.assertEqual(newest["id"], status_snapshot()["route_policy_command"]["id"])

    def test_recheck_cannot_overwrite_policy_command_status(self):
        command = self.command()
        mark_control_command(enqueue_control_command("recheck_route"), "done")
        self.assertEqual(command["id"], status_snapshot()["route_policy_command"]["id"])

    def test_failed_save_does_not_change_live_policy_or_route(self):
        supervisor = self.supervisor(DIRECT)
        with patch("proxy_xray.route_control.persist_route_policy", side_effect=OSError("disk failure")):
            apply_route_policy(supervisor, self.command())
        self.assertEqual(AUTO, supervisor.policy.route_policy)
        self.assertEqual(DIRECT, supervisor.policy.mode)
        supervisor.core.override.assert_not_called()
        self.assertEqual("failed", status_snapshot()["route_policy_command"]["status"])

    def test_direct_is_closed_before_allowed_route_probes(self):
        supervisor = self.supervisor(DIRECT)
        seen = []
        supervisor.core.override.side_effect = lambda mode: seen.append(mode) or True
        def probe(port, settings, **kwargs):
            self.assertEqual(UNAVAILABLE, seen[0])
            self.assertNotEqual(SLOTS[0]["probe_ports"][DIRECT], port)
            return Probe(True, .1)
        with patch("proxy_xray.route_control.persist_route_policy"), patch(
            "proxy_xray.supervisor.health_at", side_effect=probe
        ), patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            apply_route_policy(supervisor, self.command())
        self.assertEqual([UNAVAILABLE, CHAIN], seen)
        self.assertEqual(CHAIN, supervisor.policy.mode)

    def test_failed_block_override_stops_core_instead_of_leaving_direct(self):
        supervisor = self.supervisor(DIRECT)
        supervisor.core.override.return_value = False
        with patch("proxy_xray.route_control.persist_route_policy"), patch(
            "proxy_xray.route_control.set_dns_proxy_ready"
        ) as ready:
            apply_route_policy(supervisor, self.command())
        supervisor.core.stop.assert_called_once()
        self.assertEqual(UNAVAILABLE, supervisor.policy.mode)
        self.assertEqual(SUBSCRIPTION_ONLY, supervisor.policy.route_policy)
        ready.assert_called_with(supervisor.args, False)

    def test_same_choice_does_not_probe_or_restart(self):
        supervisor = self.supervisor(CHAIN)
        with patch("proxy_xray.route_control.persist_route_policy") as persist:
            apply_route_policy(supervisor, self.command(AUTO))
        persist.assert_not_called()
        supervisor.core.override.assert_not_called()

    def test_auto_choice_does_not_bypass_direct_guard(self):
        supervisor = self.supervisor(CHAIN)
        supervisor.policy.route_policy = SUBSCRIPTION_ONLY
        with patch("proxy_xray.route_control.persist_route_policy"), patch(
            "proxy_xray.supervisor.health_at", return_value=Probe(True, .1)
        ):
            apply_route_policy(supervisor, self.command(AUTO))
        self.assertEqual(CHAIN, supervisor.policy.mode)
        supervisor.core.override.assert_not_called()

    def test_forbidden_override_and_old_probe_job_are_rejected(self):
        supervisor = self.supervisor(CHAIN)
        supervisor.policy.route_policy = SUBSCRIPTION_ONLY
        self.assertFalse(supervisor.set_mode(DIRECT, "forbidden"))
        supervisor.jobs.append((DIRECT, candidate(), None))
        supervisor.run_probe_job()
        supervisor.inventory.probe.assert_not_called()

    def test_manual_recheck_and_matrix_do_not_queue_direct(self):
        supervisor = self.supervisor(CHAIN)
        supervisor.policy.route_policy = SUBSCRIPTION_ONLY
        supervisor.inventory.exits = [candidate()]
        supervisor.inventory.diagnostic_pairs.return_value = [(CHAIN, candidate(), candidate("subscription"))]
        queue_recheck(supervisor)
        self.assertEqual([CHAIN], [job[0] for job in supervisor.jobs])

    def test_rebuild_fallback_does_not_probe_or_select_direct(self):
        supervisor = self.supervisor(CHAIN)
        supervisor.policy.route_policy = SUBSCRIPTION_ONLY
        supervisor.core.alive.return_value = False
        supervisor.inventory.direct = set()
        supervisor.inventory.pairs = set()
        supervisor.switches = {"dns": Mock()}
        staged = Mock()
        staged.slot = SLOTS[1]
        staged.selected.return_value = (None, None, False)
        staged.override.return_value = True
        with patch("proxy_xray.supervisor.ChainCore", return_value=staged), patch(
            "proxy_xray.supervisor.health_at", side_effect=[Probe(False), Probe(False), Probe(True)]
        ) as probe, patch("proxy_xray.supervisor.set_switch_targets"), patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            self.assertTrue(supervisor.rebuild())
        self.assertEqual(SUBSCRIPTION, supervisor.policy.mode)
        self.assertTrue(all(call.args[0] != SLOTS[1]["probe_ports"][DIRECT] for call in probe.call_args_list))

    def test_subscription_ui_hides_direct_guard_and_uses_delegated_events(self):
        fragments = dashboard_v5_fragments({"route_policy": SUBSCRIPTION_ONLY, "route_mode": CHAIN})
        self.assertIn('data-route-policy="subscription_only" aria-pressed="true"', fragments["v5-current"])
        self.assertNotIn("Direct stable", fragments["v5-current"])
        page = render_dashboard_v5_html().decode()
        self.assertIn("document.addEventListener('click'", page)
        self.assertIn("key === 'v5-current' && policyRequest", page)


class RoutingPolicyHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        pop_control_commands()
        set_status(route_policy_command=None, last_control_command=None)

    def request(self, payload, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("POST", "/control/route-policy", payload,
                           headers or {"Content-Type": "application/json"})
        response = connection.getresponse()
        status, body = response.status, response.read()
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        connection.close()
        return status, body

    def test_valid_post_queues_command_and_returns_accepted(self):
        status, body = self.request(json.dumps({"policy": SUBSCRIPTION_ONLY}), {
            "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.server.server_port}"})
        self.assertEqual(202, status)
        self.assertEqual(json.loads(body)["id"], pop_control_commands()[0]["id"])

    def test_foreign_origin_is_rejected(self):
        for origin in ("https://attacker.test", f"https://127.0.0.1:{self.server.server_port}", "http://[invalid"):
            status, _ = self.request('{"policy":"auto"}', {"Content-Type": "application/json", "Origin": origin})
            self.assertEqual(403, status)
        self.assertEqual([], pop_control_commands())

    def test_cross_site_without_origin_is_rejected(self):
        status, _ = self.request('{"policy":"auto"}', {"Content-Type": "application/json", "Sec-Fetch-Site": "cross-site"})
        self.assertEqual(403, status)

    def test_invalid_json_policy_size_and_content_type_are_rejected(self):
        for payload in ("{broken", "[]", '{"policy":"direct"}', '{"policy":"auto","extra":1}', " " * 1025):
            self.assertEqual(400, self.request(payload)[0])
        self.assertEqual(400, self.request('{"policy":"auto"}', {"Content-Type": "text/plain"})[0])
        self.assertEqual([], pop_control_commands())


if __name__ == "__main__":
    unittest.main()
