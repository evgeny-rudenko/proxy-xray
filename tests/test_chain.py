import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from proxy_xray.chain_config import BALANCERS, SLOTS, make_chain_config, chain_relay_balancer, relay_tag
from proxy_xray.main import build_parser
from proxy_xray.routes import DIRECT, CHAIN, SUBSCRIPTION, UNAVAILABLE, RoutePolicy, relay_pool, split_roles
from proxy_xray.route_inventory import RouteInventory
from proxy_xray.route_probe import Probe, health_at
from proxy_xray.status import enqueue_control_command, pop_control_commands, mark_control_command
from proxy_xray.state import load_state
from proxy_xray.subscription import load_candidates
from proxy_xray.util import save_json_file
from proxy_xray.dashboard_view import dashboard_v5_fragments


def candidate(source="extra", index=0, host=None):
    return {"uri": f"synthetic-{source}-{index}", "source": source, "tag": f"proxy-{'extra' if source == 'extra' else 'sub'}-{index}",
            "host": host or f"node-{index}.example.test", "port": 443, "name": "Synthetic node",
            "outbound": {"protocol": "vless", "settings": {"vnext": [{
                "address": host or f"node-{index}.example.test", "port": 443,
                "users": [{"id": "11111111-1111-4111-8111-111111111111", "encryption": "none"}]}]},
                "streamSettings": {"network": "tcp", "security": "tls", "tlsSettings": {"serverName": "example.test"}}}}


def args():
    return build_parser().parse_args(["--sub-url", "https://example.test/sub", "--extra-file", "synthetic.txt"])


class RoutePolicyTest(unittest.TestCase):
    def test_startup_is_closed_and_uses_verified_priority(self):
        policy = RoutePolicy()
        self.assertEqual(UNAVAILABLE, policy.choose(0))
        policy.observe(SUBSCRIPTION, True, now=1)
        self.assertEqual(SUBSCRIPTION, policy.choose(1))
        policy.observe(CHAIN, True, now=2)
        self.assertEqual(CHAIN, policy.choose(2))
        policy.observe(DIRECT, True, now=3)
        self.assertEqual(DIRECT, policy.choose(3))

    def test_two_failures_switch_to_chain(self):
        policy = RoutePolicy()
        policy.commit(DIRECT, "test", now=0)
        policy.observe(CHAIN, True, now=20)
        policy.observe(DIRECT, False, now=20)
        self.assertEqual(DIRECT, policy.choose(20))
        policy.observe(DIRECT, False, now=40)
        self.assertEqual(CHAIN, policy.choose(40))

    def test_full_failure_bypasses_return_guard(self):
        policy = RoutePolicy()
        policy.commit(CHAIN, "test", now=10)
        policy.observe(CHAIN, False, now=20)
        policy.observe(CHAIN, False, now=40)
        policy.observe(SUBSCRIPTION, True, now=40)
        self.assertEqual(SUBSCRIPTION, policy.choose(40))

    def test_chain_recovery_requires_three_spaced_successes_and_hold(self):
        policy = RoutePolicy()
        policy.commit(SUBSCRIPTION, "test", now=0)
        for now in (20, 40, 60):
            policy.observe(CHAIN, True, now=now)
        self.assertEqual(SUBSCRIPTION, policy.choose(60))
        for now in (160, 180, 200):
            policy.observe(CHAIN, True, now=now)
        self.assertEqual(CHAIN, policy.choose(200))

    def test_direct_return_requires_five_minutes_of_continuous_route_health(self):
        policy = RoutePolicy()
        policy.commit(CHAIN, "test", now=0)
        for now in range(20, 320, 20):
            policy.observe(DIRECT, True, now=now)
        self.assertEqual(CHAIN, policy.choose(319.9))
        policy.observe(DIRECT, True, now=320)
        self.assertEqual(DIRECT, policy.choose(320))

    def test_direct_failure_and_missing_checks_restart_stability_window(self):
        policy = RoutePolicy()
        policy.commit(CHAIN, "test", now=0)
        for now in range(20, 220, 20):
            policy.observe(DIRECT, True, now=now)
        policy.observe(DIRECT, False, now=220)
        policy.observe(DIRECT, True, now=240)
        self.assertEqual(0, policy.direct_recovery(240)["stable_seconds"])
        policy.observe(DIRECT, True, now=300)
        self.assertEqual(0, policy.direct_recovery(300)["stable_seconds"])
        self.assertEqual(CHAIN, policy.choose(300))

    def test_pinned_proofs_cannot_accumulate_direct_return_stability(self):
        policy = RoutePolicy()
        policy.commit(CHAIN, "test", now=0)
        for now in range(20, 700, 20):
            policy.observe(DIRECT, True, now=now, recovery=False)
        self.assertEqual(CHAIN, policy.choose(680))
        self.assertEqual(0, policy.direct_recovery(680)["stable_seconds"])

    def test_stability_window_restarts_after_failure_even_without_a_long_gap(self):
        policy = RoutePolicy()
        policy.commit(CHAIN, "test", now=0)
        policy.observe(DIRECT, True, now=20)
        policy.observe(DIRECT, False, now=40)
        for now in range(60, 380, 20):
            policy.observe(DIRECT, True, now=now)
        self.assertEqual(300, policy.direct_recovery(360)["stable_seconds"])
        self.assertEqual(DIRECT, policy.choose(360))

    def test_rapid_direct_failures_delay_return_for_ten_then_fifteen_minutes(self):
        policy = RoutePolicy()
        for start, expected in ((0, 600), (800, 900), (1900, 900)):
            policy.commit(DIRECT, "test return", now=start)
            policy.observe(DIRECT, False, now=start+20)
            policy.observe(DIRECT, False, now=start+40)
            policy.commit(CHAIN, "direct failed", now=start+40)
            self.assertEqual(expected, policy.direct_recovery(start+40)["retry_remaining"])
            for now in range(start+60, start+40+expected, 20):
                policy.observe(DIRECT, True, now=now)
            self.assertEqual(CHAIN, policy.choose(start+39+expected))
            policy.observe(DIRECT, True, now=start+40+expected)
            self.assertEqual(DIRECT, policy.choose(start+40+expected))

    def test_current_route_failure_bypasses_direct_return_and_retry_wait(self):
        policy = RoutePolicy()
        policy.commit(DIRECT, "test", now=0)
        policy.observe(DIRECT, False, now=20)
        policy.observe(DIRECT, False, now=40)
        policy.commit(CHAIN, "direct failed", now=40)
        policy.observe(CHAIN, False, now=60)
        policy.observe(CHAIN, False, now=80)
        policy.observe(DIRECT, True, now=80)
        self.assertEqual(DIRECT, policy.choose(80))
        self.assertGreater(policy.direct_recovery(80)["retry_remaining"], 0)

    def test_stable_direct_operation_clears_rapid_failure_backoff(self):
        policy = RoutePolicy()
        policy.direct_fast_failures = 2
        policy.direct_retry_at = 900
        policy.commit(DIRECT, "emergency bypass", now=0)
        for now in range(20, 340, 20):
            policy.observe(DIRECT, True, now=now)
        self.assertEqual(0, policy.direct_fast_failures)
        self.assertEqual(0, policy.direct_retry_at)

    def test_short_manual_transition_does_not_invent_a_direct_failure_penalty(self):
        policy = RoutePolicy()
        policy.commit(DIRECT, "test", now=0)
        policy.commit(CHAIN, "operator transition", now=20)
        self.assertEqual(0, policy.direct_fast_failures)
        self.assertEqual(0, policy.direct_retry_at)

    def test_repeated_click_cannot_fake_three_successes(self):
        policy = RoutePolicy()
        for _ in range(10):
            policy.observe(DIRECT, True, now=20)
        self.assertEqual(1, policy.success_streaks[DIRECT])

    def test_stale_success_cannot_authorize_route(self):
        policy = RoutePolicy()
        policy.observe(DIRECT, True, now=0)
        self.assertEqual(UNAVAILABLE, policy.choose(61))

    def test_all_failed_blocks_foreign_traffic(self):
        policy = RoutePolicy()
        policy.commit(DIRECT, "test", now=0)
        policy.observe(DIRECT, False, now=20)
        policy.observe(DIRECT, False, now=40)
        self.assertEqual(UNAVAILABLE, policy.choose(40))

    def test_failed_return_probe_resets_streak(self):
        policy = RoutePolicy()
        policy.observe(DIRECT, True, now=20)
        policy.observe(DIRECT, False, now=40)
        self.assertEqual(0, policy.success_streaks[DIRECT])


class ChainConfigTest(unittest.TestCase):
    def setUp(self):
        self.exits = [candidate(index=i) for i in range(4)]
        self.relays = [candidate("subscription", i) for i in range(3)]
        self.config, self.mapping = make_chain_config(self.exits, self.relays, args(), SLOTS[0])

    def test_role_filters_do_not_mix_sources(self):
        exits, relays = split_roles(self.exits + self.relays)
        self.assertTrue(all(c["source"] == "extra" for c in exits))
        self.assertTrue(all(c["source"] == "subscription" for c in relays))

    def test_three_verified_relays_prefer_different_hosts(self):
        entries = [candidate("subscription", i, "same.test" if i < 2 else f"node-{i}.test") for i in range(5)]
        pool = relay_pool(entries, {c["uri"] for c in entries}, 3)
        self.assertEqual(3, len(pool))
        self.assertEqual(3, len({c["host"] for c in pool}))

    def test_unverified_relay_is_never_in_pool(self):
        self.assertEqual([], relay_pool(self.relays, set()))

    def test_selectors_are_disjoint(self):
        selectors = {b["tag"]: b["selector"] for b in self.config["routing"]["balancers"]}
        self.assertEqual(["relay-proxy-"], selectors["relays"])
        self.assertEqual(["exit-direct-"], selectors["amsterdam-direct"])
        self.assertEqual(["exit-chain-"], selectors["amsterdam-chain"])

    def test_chain_keeps_tls_and_uses_loopback_dialer(self):
        outbound = next(o for o in self.config["outbounds"] if o["tag"].startswith("exit-chain-"))
        self.assertEqual("relay-loop-proxy-extra-0", outbound["streamSettings"]["sockopt"]["dialerProxy"])
        self.assertEqual("example.test", outbound["streamSettings"]["tlsSettings"]["serverName"])

    def test_loopback_stage_precedes_ru_and_default(self):
        rules = self.config["routing"]["rules"]
        stage = next(i for i, r in enumerate(rules) if r.get("inboundTag") == ["stage-relay"])
        direct = next(i for i, r in enumerate(rules) if r.get("outboundTag") == "direct")
        self.assertLess(stage, direct)
        self.assertEqual("relays", rules[stage]["balancerTag"])

    def test_dns_bypasses_ru_rules_and_uses_egress(self):
        rules = self.config["routing"]["rules"]
        rule = next(r for r in rules if r.get("inboundTag") == ["global-dns"])
        self.assertEqual("egress", rule["balancerTag"])
        self.assertLess(rules.index(rule), next(i for i, r in enumerate(rules) if r.get("outboundTag") == "direct"))

    def test_unverified_startup_fallbacks_are_blackholes(self):
        self.assertTrue(all(b["fallbackTag"] == "block" for b in self.config["routing"]["balancers"][:2]))
        self.assertEqual("block", self.config["routing"]["balancers"][-1]["fallbackTag"])

    def test_all_core_and_probe_ports_are_loopback_only(self):
        self.assertTrue(all(i["listen"] == "127.0.0.1" for i in self.config["inbounds"]))

    def test_builder_does_not_mutate_secret_source(self):
        self.assertNotIn("sockopt", self.exits[0]["outbound"]["streamSettings"])

    def test_chain_only_uses_relays_with_a_verified_pair(self):
        pairs = {(self.exits[0]["uri"], self.relays[1]["uri"])}
        config, _ = make_chain_config(self.exits, self.relays, args(), SLOTS[0], confirmed_pairs=pairs)
        pair_balancer = next(b for b in config["routing"]["balancers"]
                             if b["tag"] == chain_relay_balancer(self.exits[0]))
        self.assertEqual([relay_tag(self.relays[1])], pair_balancer["selector"])
        self.assertEqual(relay_tag(self.relays[1]), pair_balancer["fallbackTag"])
        missing = next(b for b in config["routing"]["balancers"]
                       if b["tag"] == chain_relay_balancer(self.exits[1]))
        self.assertEqual("block", missing["fallbackTag"])

    def test_exact_relay_selector_cannot_prefix_match_another_node(self):
        short = candidate("subscription", 1)
        long = candidate("subscription", 10)
        self.assertFalse(relay_tag(long).startswith(relay_tag(short)))


class RouteRuntimeTest(unittest.TestCase):
    def test_failed_startup_subscription_can_use_cached_relays_without_trusting_them(self):
        exit_candidate, relay = candidate(), candidate("subscription")
        with patch("proxy_xray.subscription.load_extra_candidates", return_value=[exit_candidate]), patch(
            "proxy_xray.subscription.fetch_subscription", side_effect=OSError("offline")
        ), patch("proxy_xray.subscription.load_cached_subscription_candidates", return_value=[relay]), patch(
            "proxy_xray.subscription.filter_and_rank", side_effect=lambda entries, *_: entries
        ):
            candidates = load_candidates(args(), state={})
        inventory = RouteInventory(args(), candidates)
        self.assertEqual(1, len(inventory.exits))
        self.assertEqual(1, len(inventory.relays))
        self.assertEqual([], inventory.pool())

    def test_failed_startup_subscription_can_use_extras_only(self):
        with patch("proxy_xray.subscription.load_extra_candidates", return_value=[candidate()]), patch(
            "proxy_xray.subscription.fetch_subscription", side_effect=OSError("offline")
        ):
            candidates = load_candidates(args(), state={})
        self.assertEqual(["extra"], [c["source"] for c in candidates])

    def test_bootstrap_failure_does_not_penalize_relay_or_run_network_probe(self):
        relay = candidate("subscription")
        inventory = RouteInventory(args(), [candidate(), relay])
        inventory.verified_relays.add(relay["uri"])
        with patch.object(inventory.resolver, "resolve", return_value=False), patch(
            "proxy_xray.route_inventory.pinned_probe"
        ) as probe:
            self.assertFalse(inventory.probe(SUBSCRIPTION, relay).ok)
        probe.assert_not_called()
        self.assertIn(relay["uri"], inventory.verified_relays)
        self.assertNotIn("last_fail_at", relay)

    def test_chain_failure_does_not_penalize_relay(self):
        exit_candidate, relay = candidate(), candidate("subscription")
        inventory = RouteInventory(args(), [exit_candidate, relay])
        inventory.verified_relays.add(relay["uri"])
        with patch.object(inventory.resolver, "resolve", return_value=True), patch(
            "proxy_xray.route_inventory.pinned_probe", return_value=Probe(False, reason="exit failed", time=1)
        ):
            inventory.probe(CHAIN, exit_candidate, relay)
        self.assertIn(relay["uri"], inventory.verified_relays)
        self.assertNotIn("last_fail_at", relay)

    def test_saved_pair_history_is_not_a_startup_proof(self):
        inventory = RouteInventory(args(), [candidate(), candidate("subscription")], {"old": {"checks": [{"ok": True}]}})
        self.assertEqual(set(), inventory.pairs)
        self.assertEqual([], inventory.pool())

    def test_subscription_removal_removes_session_proof(self):
        c = candidate("subscription")
        inventory = RouteInventory(args(), [candidate(), c])
        inventory.verified_relays.add(c["uri"])
        inventory.update([candidate()])
        self.assertEqual([], inventory.pool())

    def test_bad_state_json_does_not_crash_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            with open(path, "w") as handle:
                handle.write("{broken")
            self.assertEqual({}, load_state(path)["candidates"])

    def test_atomic_write_failure_retains_previous_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            save_json_file(path, {"old": True})
            with patch("proxy_xray.util.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    save_json_file(path, {"new": True})
            with open(path) as handle:
                self.assertEqual({"old": True}, json.load(handle))
            self.assertEqual(["state.json"], os.listdir(directory))

    def test_health_tries_independent_backup(self):
        with patch("proxy_xray.route_probe.curl_probe", side_effect=[
            {"status": "fail"}, {"status": "ok", "latency": 0.1}
        ]) as probe:
            self.assertTrue(health_at(12345, args()).ok)
            self.assertEqual(2, probe.call_count)

    def test_recheck_commands_are_coalesced(self):
        pop_control_commands()
        first = enqueue_control_command("recheck_route")
        second = enqueue_control_command("recheck_route")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual([first], pop_control_commands())
        mark_control_command(first, "done")

    def test_dashboard_shows_emergency_exit_and_unknown_transport(self):
        fragments = dashboard_v5_fragments({"route_mode": SUBSCRIPTION, "health_checks": {}, "exit": {}})
        self.assertIn("Exit point changed", fragments["v5-overview"])
        self.assertIn("Transport UNKNOWN", fragments["v5-current"])

    def test_dashboard_shows_direct_retry_wait_instead_of_expired_general_hold(self):
        fragments = dashboard_v5_fragments({"route_mode": CHAIN, "route_transition": {
            "hold_remaining": 0, "direct_recovery": {"retry_remaining": 600,
                "stable_remaining": 260, "stable_seconds": 40, "required_stable_seconds": 300}}})
        self.assertIn("<strong>600s</strong>", fragments["v5-current"])
        self.assertIn("Direct stable 40 / 300s", fragments["v5-current"])
        self.assertNotIn("Hot standby", fragments["v5-current"])


if __name__ == "__main__":
    unittest.main()
