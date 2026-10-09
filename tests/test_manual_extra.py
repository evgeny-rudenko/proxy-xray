import unittest
from types import SimpleNamespace
from unittest.mock import patch

from proxy_xray.candidate_checker import ExtraProbeResult, check_extra_candidate
from proxy_xray.manual_extra import (
    activate_extra_slot,
    extra_warmup_seconds,
    manual_extra_order,
    remaining_extra_candidates,
    stage_verified_extra_pool,
    verify_extra_candidates,
)
from proxy_xray.slot_manager import stop_slot
from proxy_xray.xray_process import start_native_xray


def candidate(name, network, security, uri=None, failures=0, consecutive_failures=0, latency=0.1):
    return {
        "index": 0,
        "uri": uri or f"vless://{name}",
        "name": name,
        "source": "extra",
        "last_latency": latency,
        "outbound": {"streamSettings": {"network": network, "security": security}},
        "quality": {
            "checks": 10,
            "success_rate": 1.0,
            "consecutive_failures": consecutive_failures,
            "last_24h_failures": failures,
            "latency_ewma": latency,
        },
    }


class ManualExtraTest(unittest.TestCase):
    def test_grpc_and_xhttp_are_preferred_fallback_transports(self):
        grpc = candidate("grpc", "grpc", "tls", latency=0.05)
        reality = candidate("reality", "tcp", "reality", latency=0.3)
        tls = candidate("tls", "tcp", "tls", latency=0.2)
        xhttp = candidate("xhttp", "xhttp", "tls", latency=0.04)

        ordered = manual_extra_order([grpc, xhttp, tls, reality])

        self.assertEqual(["grpc", "xhttp", "reality", "tls"], [item["name"] for item in ordered])

    def test_current_failure_streak_is_considered_before_transport(self):
        unreliable_grpc = candidate("grpc", "grpc", "tls", consecutive_failures=1)
        stable_reality = candidate("reality", "tcp", "reality")

        ordered = manual_extra_order([unreliable_grpc, stable_reality])

        self.assertEqual(["reality", "grpc"], [item["name"] for item in ordered])

    def test_failed_candidate_is_removed_from_recovery_pool(self):
        failed = candidate("grpc", "grpc", "tls")
        reality = candidate("reality", "tcp", "reality")

        remaining = remaining_extra_candidates([failed, reality], failed)

        self.assertEqual(["reality"], [item["name"] for item in remaining])

    def test_subscription_is_never_probed(self):
        extra = candidate("extra", "grpc", "tls")
        subscription = dict(extra, source="subscription")
        args = SimpleNamespace(quarantine_duration=900)
        with patch("proxy_xray.manual_extra.check_extra_candidate", return_value=ExtraProbeResult(True, 0.1, 5000)) as probe:
            verified, removed = verify_extra_candidates([extra, subscription], args, "test")
        self.assertEqual([extra], verified)
        self.assertEqual([], removed)
        probe.assert_called_once()

    def test_only_independently_failed_candidate_is_quarantined(self):
        grpc = candidate("grpc", "grpc", "tls")
        reality = candidate("reality", "tcp", "reality")
        args = SimpleNamespace(quarantine_duration=900)
        with patch("proxy_xray.manual_extra.check_extra_candidate", side_effect=[ExtraProbeResult(True, 0.1, 5000), ExtraProbeResult(False, reason="two consecutive health failures")]):
            verified, removed = verify_extra_candidates([reality, grpc], args, "test")
        self.assertEqual([grpc], verified)
        self.assertEqual([reality], removed)
        self.assertNotIn("quarantine_until", grpc)
        self.assertIn("quarantine_until", reality)

    def test_fresh_verification_overrides_stale_grpc_failure_history(self):
        grpc = candidate("grpc", "grpc", "tls", consecutive_failures=3)
        tls = candidate("tls", "tcp", "tls")
        with patch("proxy_xray.manual_extra.check_extra_candidate", return_value=ExtraProbeResult(True, 0.1, 5000)):
            verified, _ = verify_extra_candidates([grpc, tls], SimpleNamespace(), "test")
        self.assertEqual([grpc, tls], verified)

    def test_local_probe_error_is_not_remote_failure(self):
        grpc = candidate("grpc", "grpc", "tls")
        with patch("proxy_xray.manual_extra.check_extra_candidate", side_effect=OSError("local startup error")):
            verified, removed = verify_extra_candidates([grpc], SimpleNamespace(), "test")
        self.assertEqual(([], []), (verified, removed))
        self.assertNotIn("quarantine_until", grpc)

    def test_native_warmup_accounts_for_every_transport(self):
        args = SimpleNamespace(observatory_probe_interval="10s", candidate_check_start_delay=1)
        self.assertEqual(45, extra_warmup_seconds(args, 4))
        self.assertEqual(2, extra_warmup_seconds(args, 1))

    def test_handoff_stops_old_active_only_after_switch_and_keeps_reserve(self):
        active, staged, standby = {"name": "old"}, {"name": "new"}, {"name": "reserve"}
        events = []
        with patch("proxy_xray.manual_extra.set_switch_targets", side_effect=lambda switches, slot: events.append(("switch", slot))), patch("proxy_xray.manual_extra.stop_slot", side_effect=lambda slot: events.append(("stop", slot))):
            result = activate_extra_slot(active, staged, {})
        self.assertIs(result, staged)
        self.assertEqual([("switch", staged), ("stop", active)], events)
        self.assertNotIn(("stop", standby), events)

    def test_failed_balancer_can_stage_verified_grpc_alone(self):
        grpc, tls = candidate("grpc", "grpc", "tls"), candidate("tls", "tcp", "tls")
        args = SimpleNamespace(health_url="https://example.com", health_timeout=2, quality_url="https://example.com/data", quality_max_time=3, quality_min_kbps=1000)
        slot = {"socks_port": 12345}
        with patch("proxy_xray.manual_extra.start_slot") as start, patch("proxy_xray.manual_extra.stop_slot") as stop, patch("proxy_xray.manual_extra.extra_warmup_seconds", return_value=0), patch("proxy_xray.manual_extra.time.sleep"), patch("proxy_xray.manual_extra.check_slot", side_effect=[(False, None)] * 4 + [(True, 0.1)] * 2), patch("proxy_xray.manual_extra.throughput_check", return_value=(True, 5000)):
            pool, latency, kbps = stage_verified_extra_pool(slot, [grpc, tls], args, {}, {})
        self.assertEqual([grpc], pool)
        self.assertEqual((0.1, 5000), (latency, kbps))
        self.assertEqual(2, start.call_count)
        stop.assert_called_once_with(slot)
        self.assertNotIn("quarantine_until", tls)

    def test_all_staged_failures_clean_up_without_quarantining(self):
        grpc = candidate("grpc", "grpc", "tls")
        slot = {"socks_port": 12345}
        with patch("proxy_xray.manual_extra.start_slot"), patch("proxy_xray.manual_extra.stop_slot") as stop, patch("proxy_xray.manual_extra.extra_warmup_seconds", return_value=0), patch("proxy_xray.manual_extra.time.sleep"), patch("proxy_xray.manual_extra.check_slot", return_value=(False, None)):
            result = stage_verified_extra_pool(slot, [grpc], SimpleNamespace(), {}, {})
        self.assertEqual(([], None, 0.0), result)
        stop.assert_called_once_with(slot)
        self.assertNotIn("quarantine_until", grpc)

    def test_temporary_slot_config_is_removed_on_stop(self):
        slot = {"config_path": "/tmp/example.json", "temporary_config": True, "proc": None}
        with patch("proxy_xray.slot_manager.os.unlink") as unlink:
            stop_slot(slot)
        unlink.assert_called_once_with("/tmp/example.json")

    def test_staging_config_listens_only_on_loopback(self):
        grpc = dict(candidate("grpc", "grpc", "tls"), tag="proxy-extra-0", host="example.com", port=443)
        config = {"inbounds": [{"listen": "0.0.0.0"}, {"listen": "127.0.0.1"}]}
        with patch("proxy_xray.xray_process.make_native_balancer_config", return_value=config), patch("proxy_xray.xray_process.write_config") as write, patch("proxy_xray.xray_process.subprocess.Popen"):
            start_native_xray([grpc], SimpleNamespace(dns="127.0.0.1", debug=False), {}, {}, update_status_config=False, preserve_order=True, inbound_listen="127.0.0.1")
        self.assertTrue(all(inbound["listen"] == "127.0.0.1" for inbound in write.call_args.args[1]["inbounds"]))


class PinnedExtraProbeTest(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(candidate_check_start_delay=1, candidate_check_timeout=2, health_url="https://example.com", quality_url="https://example.com/data", quality_max_time=3, quality_min_kbps=1000)
        self.mocks = {}
        for name in ("free_local_port", "write_candidate_config", "subprocess.Popen", "time.sleep", "curl_check", "throughput_check", "terminate_process", "os.unlink"):
            patcher = patch("proxy_xray.candidate_checker." + name)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.mocks["free_local_port"].return_value = 12345
        self.mocks["write_candidate_config"].return_value = "/tmp/example.json"
        self.mocks["throughput_check"].return_value = (True, 5000)

    def test_transient_failure_needs_two_consecutive_successes(self):
        self.mocks["curl_check"].side_effect = [(False, None), (True, 0.1), (True, 0.2)]
        result = check_extra_candidate(candidate("grpc", "grpc", "tls"), self.args)
        self.assertTrue(result.ok)
        self.assertEqual(3, self.mocks["curl_check"].call_count)
        self.mocks["terminate_process"].assert_called_once()
        self.mocks["os.unlink"].assert_called_once_with("/tmp/example.json")

    def test_two_health_failures_reject_without_download(self):
        self.mocks["curl_check"].return_value = (False, None)
        result = check_extra_candidate(candidate("grpc", "grpc", "tls"), self.args)
        self.assertFalse(result.ok)
        self.assertEqual(2, self.mocks["curl_check"].call_count)
        self.mocks["throughput_check"].assert_not_called()

    def test_quality_download_is_retried(self):
        self.mocks["curl_check"].return_value = (True, 0.1)
        self.mocks["throughput_check"].side_effect = [(False, 0), (True, 5000)]
        self.assertTrue(check_extra_candidate(candidate("grpc", "grpc", "tls"), self.args).ok)
        self.assertEqual(2, self.mocks["throughput_check"].call_count)

    def test_process_start_failure_still_removes_config(self):
        self.mocks["subprocess.Popen"].side_effect = OSError("startup")
        with self.assertRaises(OSError):
            check_extra_candidate(candidate("grpc", "grpc", "tls"), self.args)
        self.mocks["os.unlink"].assert_called_once_with("/tmp/example.json")


if __name__ == "__main__":
    unittest.main()
