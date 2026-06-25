import time
import unittest

from proxy_xray.pool import select_active_pool, select_standby_pool


def candidate(name, uri, source="subscription", region_score=1, last_ok_at=None, last_fail_at=None):
    return {
        "index": 0,
        "uri": uri,
        "name": name,
        "host": f"{name}.example.com",
        "port": 443,
        "source": source,
        "source_score": 0 if source == "extra" else 1,
        "region_score": region_score,
        "network_score": 0,
        "last_ok_at": last_ok_at,
        "last_fail_at": last_fail_at,
        "last_latency": None,
        "last_throughput_kbps": None,
        "quarantine_until": None,
        "quarantine_reason": None,
        "outbound": {
            "tag": name,
            "streamSettings": {"network": "tcp", "security": "reality"},
        },
    }


class PoolSelectionTest(unittest.TestCase):
    def test_active_pool_keeps_current_candidate_first(self):
        now = time.time()
        current = candidate("slow-current", "vless://current", region_score=2, last_ok_at=now)
        better = candidate("better-extra", "vless://better", source="extra", last_ok_at=now)

        pool = select_active_pool([better, current], active_candidate=current, size=2, now=now)

        self.assertEqual(["vless://current", "vless://better"], [item["uri"] for item in pool])

    def test_standby_pool_excludes_active_pool(self):
        now = time.time()
        active = candidate("active", "vless://active", source="extra", last_ok_at=now)
        standby = candidate("standby", "vless://standby", last_ok_at=now)

        pool = select_standby_pool([active, standby], active_pool=[active], size=1, now=now)

        self.assertEqual(["vless://standby"], [item["uri"] for item in pool])

    def test_standby_pool_prefers_recent_live_before_cold(self):
        now = time.time()
        active = candidate("active", "vless://active", source="extra", last_ok_at=now)
        live = candidate("live", "vless://live", last_ok_at=now)
        cold = candidate("cold", "vless://cold")

        pool = select_standby_pool([cold, live, active], active_pool=[active], size=1, max_age=600, now=now)

        self.assertEqual(["vless://live"], [item["uri"] for item in pool])

    def test_standby_pool_falls_back_to_cold_when_no_live_exists(self):
        active = candidate("active", "vless://active", source="extra")
        cold = candidate("cold", "vless://cold")

        pool = select_standby_pool([active, cold], active_pool=[active], size=1)

        self.assertEqual(["vless://cold"], [item["uri"] for item in pool])

    def test_quarantined_candidates_are_excluded(self):
        now = time.time()
        active = candidate("active", "vless://active", source="extra", last_ok_at=now)
        quarantined = candidate("quarantined", "vless://quarantined", last_ok_at=now)
        quarantined["quarantine_until"] = now + 3600
        healthy = candidate("healthy", "vless://healthy", last_ok_at=now)

        pool = select_standby_pool(
            [active, quarantined, healthy],
            active_pool=[active],
            size=2,
            now=now,
        )

        self.assertEqual(["vless://healthy"], [item["uri"] for item in pool])


if __name__ == "__main__":
    unittest.main()
