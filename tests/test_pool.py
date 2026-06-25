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


def candidate_with_host(name, uri, host, source="subscription", region_score=1, last_ok_at=None):
    item = candidate(name, uri, source=source, region_score=region_score, last_ok_at=last_ok_at)
    item["host"] = host
    return item


class PoolSelectionTest(unittest.TestCase):
    def test_single_candidate_active_pool_keeps_current_candidate(self):
        now = time.time()
        current = candidate("slow-current", "vless://current", region_score=2, last_ok_at=now)
        better = candidate("better-extra", "vless://better", source="extra", last_ok_at=now)

        pool = select_active_pool([better, current], active_candidate=current, size=1, now=now)

        self.assertEqual(["vless://current"], [item["uri"] for item in pool])

    def test_multi_candidate_active_pool_uses_score_order(self):
        now = time.time()
        current = candidate("slow-current", "vless://current", region_score=2, last_ok_at=now)
        better = candidate("better-extra", "vless://better", source="extra", last_ok_at=now)

        pool = select_active_pool([better, current], active_candidate=current, size=2, now=now)

        self.assertEqual(["vless://better", "vless://current"], [item["uri"] for item in pool])

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

    def test_active_pool_limits_same_host_candidates(self):
        now = time.time()
        current = candidate_with_host("extra-0", "vless://extra-0", "same.example.com", source="extra", last_ok_at=now)
        current["last_fail_at"] = now
        extra_1 = candidate_with_host("extra-1", "vless://extra-1", "same.example.com", source="extra", last_ok_at=now)
        extra_2 = candidate_with_host("extra-2", "vless://extra-2", "same.example.com", source="extra", last_ok_at=now)
        subscription = candidate_with_host("sub-0", "vless://sub-0", "other.example.com", last_ok_at=now)

        pool = select_active_pool(
            [extra_2, extra_1, subscription, current],
            active_candidate=current,
            size=4,
            now=now,
        )

        self.assertEqual(["vless://extra-1", "vless://extra-2", "vless://sub-0"], [item["uri"] for item in pool])


if __name__ == "__main__":
    unittest.main()
