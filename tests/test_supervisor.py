import tempfile
import unittest
from unittest.mock import Mock, patch

from test_chain import args, candidate
from proxy_xray.chain_config import SLOTS, BALANCERS, chain_relay_balancer
from proxy_xray.route_status import publish
from proxy_xray.route_probe import Probe
from proxy_xray.routes import DIRECT, CHAIN, SUBSCRIPTION, UNAVAILABLE
from proxy_xray.supervisor import Supervisor


class SupervisorTest(unittest.TestCase):
    def test_inactive_pinned_success_does_not_count_toward_direct_return_guard(self):
        supervisor = self.supervisor(CHAIN)
        exit_candidate = candidate()
        supervisor.inventory.pool.return_value = []
        supervisor.inventory.direct = {exit_candidate["uri"]}
        supervisor.inventory.pairs = set()
        supervisor.inventory.probe.return_value = Probe(True, .1)
        supervisor.jobs.append((DIRECT, exit_candidate, None))
        supervisor.run_probe_job()
        self.assertEqual(0, supervisor.policy.direct_recovery()["stable_seconds"])
        self.assertNotIn(DIRECT, supervisor.policy.success_streaks)

    def test_pinned_success_cannot_reset_failures_of_current_balanced_route(self):
        supervisor = self.supervisor(DIRECT)
        exit_candidate = candidate()
        supervisor.inventory.pool.return_value = []
        supervisor.inventory.direct = {exit_candidate["uri"]}
        supervisor.inventory.pairs = set()
        supervisor.inventory.probe.return_value = Probe(True, .1)
        supervisor.jobs.append((DIRECT, exit_candidate, None))
        supervisor.run_probe_job()
        self.assertEqual(1, supervisor.policy.failures)

    def test_matrix_can_add_new_relay_while_an_old_pair_is_pending(self):
        supervisor = self.supervisor(DIRECT)
        exit_candidate = candidate()
        first, second = candidate("subscription", 0), candidate("subscription", 1)
        supervisor.jobs.append((CHAIN, exit_candidate, first))
        supervisor.inventory.diagnostic_pairs.return_value = [(CHAIN, exit_candidate, first), (CHAIN, exit_candidate, second)]
        supervisor.queue_matrix()
        self.assertEqual(2, len(supervisor.jobs))
        self.assertEqual(second, supervisor.jobs[-1][2])

    def test_chain_status_reads_its_exit_specific_relay_not_emergency_selection(self):
        supervisor = self.supervisor(CHAIN)
        exit_candidate = candidate()
        first, second = candidate("subscription", 0), candidate("subscription", 1)
        supervisor.core.config = {}
        def selected(balancer):
            item = second if balancer == chain_relay_balancer(exit_candidate) else first if balancer == BALANCERS[SUBSCRIPTION] else exit_candidate
            return item["tag"], item, True
        supervisor.core.selected.side_effect = selected
        supervisor.inventory.candidates = [exit_candidate, first, second]
        supervisor.inventory.exits = [exit_candidate]
        supervisor.inventory.pool.return_value = [first, second]
        with patch("proxy_xray.route_status.set_status") as status:
            publish(supervisor.args, supervisor.inventory, supervisor.policy, supervisor.core, None)
        self.assertEqual(second["tag"], status.call_args.kwargs["relay"]["transport"]["tag"])

    def supervisor(self, mode):
        supervisor = Supervisor(args())
        supervisor.core = Mock()
        supervisor.core.slot = SLOTS[0]
        supervisor.core.mapping = {}
        supervisor.core.override.return_value = True
        supervisor.inventory = Mock()
        supervisor.inventory.pool.return_value = [{}]
        supervisor.inventory.diagnostic_pairs.return_value = []
        supervisor.inventory.exits = []
        supervisor.inventory.candidates = []
        supervisor.policy.commit(mode, "test", now=0)
        supervisor.policy.failures = 1
        supervisor.save = Mock()
        return supervisor

    def test_chain_precedes_emergency_exit_when_direct_fails(self):
        supervisor = self.supervisor(DIRECT)
        with patch("proxy_xray.supervisor.health_at", side_effect=[
            Probe(False), Probe(True, 0.1), Probe(True, 0.1)
        ]), patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            supervisor.check_routes()
        self.assertEqual(CHAIN, supervisor.policy.mode)

    def test_route_failure_log_is_not_reported_as_a_preference_return(self):
        supervisor = self.supervisor(DIRECT)
        with patch("proxy_xray.supervisor.health_at", side_effect=[
            Probe(False), Probe(True, .1), Probe(True, .1)
        ]), patch("proxy_xray.supervisor.set_dns_proxy_ready"), patch("proxy_xray.supervisor.log") as log:
            supervisor.check_routes()
        self.assertTrue(any("direct_amsterdam failed; verified alternate route" in call.args[0]
                            for call in log.call_args_list))

    def test_restored_direct_precedes_subscription_after_chain_failure(self):
        supervisor = self.supervisor(CHAIN)
        with patch("proxy_xray.supervisor.health_at", side_effect=[
            Probe(False), Probe(True, 0.1), Probe(True, 0.1)
        ]), patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            supervisor.check_routes()
        self.assertEqual(DIRECT, supervisor.policy.mode)

    def test_failed_api_override_does_not_commit_route(self):
        supervisor = self.supervisor(DIRECT)
        supervisor.core.override.return_value = False
        with patch("proxy_xray.supervisor.health_at", side_effect=[
            Probe(False), Probe(True, 0.1), Probe(True, 0.1)
        ]):
            supervisor.check_routes()
        self.assertEqual(DIRECT, supervisor.policy.mode)
        supervisor.save.assert_not_called()

    def test_total_failure_closes_dns_readiness(self):
        supervisor = self.supervisor(DIRECT)
        with patch("proxy_xray.supervisor.health_at", return_value=Probe(False)), patch(
            "proxy_xray.supervisor.set_dns_proxy_ready"
        ) as ready:
            supervisor.check_routes()
        self.assertEqual(UNAVAILABLE, supervisor.policy.mode)
        ready.assert_called_with(supervisor.args, False)

    def test_dead_core_can_restart_in_emergency_mode(self):
        supervisor = self.supervisor(DIRECT)
        supervisor.core.alive.return_value = False
        supervisor.inventory.exits = []
        supervisor.inventory.direct = set()
        supervisor.inventory.pairs = set()
        supervisor.switches = {"dns": Mock()}
        staged = Mock()
        staged.override.return_value = True
        with patch("proxy_xray.supervisor.ChainCore", return_value=staged), patch(
            "proxy_xray.supervisor.health_at", side_effect=[Probe(False), Probe(False), Probe(False), Probe(True)]
        ), patch("proxy_xray.supervisor.set_switch_targets"), patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            self.assertTrue(supervisor.rebuild())
        self.assertEqual(SUBSCRIPTION, supervisor.policy.mode)

    def test_removed_subscription_cannot_survive_failed_preflight(self):
        supervisor = self.supervisor(DIRECT)
        supervisor.core.mapping = {"relay-retired": {"uri": "removed"}}
        supervisor.core.config = {"retired": True}
        supervisor.inventory.direct = set()
        supervisor.inventory.pairs = set()
        supervisor.switches = {"dns": Mock()}
        staged = Mock()
        staged.override.return_value = True
        with patch("proxy_xray.supervisor.make_chain_config", return_value=({}, {})), patch(
            "proxy_xray.supervisor.ChainCore", return_value=staged
        ), patch("proxy_xray.supervisor.health_at", return_value=Probe(False)), patch(
            "proxy_xray.supervisor.set_switch_targets"
        ), patch("proxy_xray.supervisor.set_dns_proxy_ready"):
            self.assertTrue(supervisor.rebuild())
        self.assertEqual(UNAVAILABLE, supervisor.policy.mode)
        self.assertIs(staged, supervisor.core)


if __name__ == "__main__":
    unittest.main()
