import unittest
from types import SimpleNamespace

from proxy_xray.failover import evaluate_cooldown, evaluate_failover, failover_state


def args(**overrides):
    defaults = {
        "max_failures": 2,
        "hot_standby_fast_failures": 1,
        "degrade_checks": 3,
        "degrade_latency": 3.0,
        "quality_degrade_checks": 2,
        "quality_min_kbps": 1000,
        "throughput_degrade_checks": 0,
        "throughput_min_kbps": 1500,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FailoverDecisionTest(unittest.TestCase):
    def test_full_failure_wins_over_fast_standby_failure(self):
        decision = evaluate_failover(args(), failures=2, standby_ready=True)

        self.assertTrue(decision.triggered)
        self.assertEqual("connection_failed", decision.kind)
        self.assertTrue(decision.full_failure)

    def test_healthy_standby_allows_fast_failure(self):
        decision = evaluate_failover(args(), failures=1, standby_ready=True)

        self.assertTrue(decision.triggered)
        self.assertEqual("active_path_failed", decision.kind)
        self.assertTrue(decision.full_failure)

    def test_fast_failure_waits_without_healthy_standby(self):
        decision = evaluate_failover(args(), failures=1, standby_ready=False)

        self.assertFalse(decision.triggered)

    def test_latency_degradation_triggers_non_full_failure(self):
        decision = evaluate_failover(args(), slow_checks=3, standby_ready=True)

        self.assertTrue(decision.triggered)
        self.assertEqual("latency_degraded", decision.kind)
        self.assertFalse(decision.full_failure)

    def test_quality_degradation_triggers_non_full_failure(self):
        decision = evaluate_failover(args(), quality_slow_checks=2, standby_ready=True)

        self.assertTrue(decision.triggered)
        self.assertEqual("quality_degraded", decision.kind)
        self.assertFalse(decision.full_failure)

    def test_disabled_throughput_degradation_does_not_trigger(self):
        decision = evaluate_failover(
            args(throughput_degrade_checks=0),
            throughput_slow_checks=5,
            standby_ready=True,
        )

        self.assertFalse(decision.triggered)

    def test_cooldown_suppresses_degradation_but_not_full_failure(self):
        degraded = evaluate_failover(args(), slow_checks=3, standby_ready=True)
        full = evaluate_failover(args(), failures=2, standby_ready=True)

        self.assertTrue(evaluate_cooldown(degraded, cooldown_until=150.0, now=100.0).suppressed)
        self.assertFalse(evaluate_cooldown(full, cooldown_until=150.0, now=100.0).suppressed)

    def test_failover_state_reports_counters_and_cooldown(self):
        state = failover_state(
            state="cooldown",
            cooldown_until=150.0,
            now_monotonic=100.0,
            standby_ready=True,
            failures=1,
            slow_checks=2,
        )

        self.assertEqual("cooldown", state["state"])
        self.assertEqual(50, state["cooldown_remaining"])
        self.assertEqual(1, state["failures"])
        self.assertEqual(2, state["slow_checks"])


if __name__ == "__main__":
    unittest.main()
