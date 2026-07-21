import unittest

from proxy_xray.manual_extra import manual_extra_order, remaining_extra_candidates


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
    def test_reality_tcp_is_preferred_over_lower_latency_grpc(self):
        grpc = candidate("grpc", "grpc", "tls", latency=0.05)
        reality = candidate("reality", "tcp", "reality", latency=0.3)
        tls = candidate("tls", "tcp", "tls", latency=0.2)
        xhttp = candidate("xhttp", "xhttp", "tls", latency=0.04)

        ordered = manual_extra_order([grpc, xhttp, tls, reality])

        self.assertEqual(["reality", "tls", "grpc", "xhttp"], [item["name"] for item in ordered])

    def test_current_failure_streak_is_considered_before_transport(self):
        unreliable_reality = candidate("reality", "tcp", "reality", consecutive_failures=1)
        stable_grpc = candidate("grpc", "grpc", "tls")

        ordered = manual_extra_order([unreliable_reality, stable_grpc])

        self.assertEqual(["grpc", "reality"], [item["name"] for item in ordered])

    def test_failed_candidate_is_removed_from_recovery_pool(self):
        failed = candidate("grpc", "grpc", "tls")
        reality = candidate("reality", "tcp", "reality")

        remaining = remaining_extra_candidates([failed, reality], failed)

        self.assertEqual(["reality"], [item["name"] for item in remaining])


if __name__ == "__main__":
    unittest.main()
