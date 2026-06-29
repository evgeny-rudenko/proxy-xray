import unittest

from proxy_xray.vless import candidate_fallback_score, region_score


def candidate(last_xray_selected_at=None):
    return {
        "source": "subscription",
        "region_score": 1,
        "network_score": 1,
        "last_ok_at": 1000.0,
        "last_fail_at": None,
        "last_latency": 0.2,
        "last_throughput_kbps": None,
        "quality": {},
        "quarantine_until": None,
        "last_xray_selected_at": last_xray_selected_at,
    }


def region_candidate(name, host):
    return {
        "name": name,
        "host": host,
    }


class VlessScoreTest(unittest.TestCase):
    def test_recent_xray_selection_adds_score_reason(self):
        score, reasons = candidate_fallback_score(candidate(last_xray_selected_at=1990.0), now=2000.0)
        base_score, _base_reasons = candidate_fallback_score(candidate(), now=2000.0)

        self.assertGreater(score, base_score)
        self.assertTrue(any("xray-selected" in reason for reason in reasons))

    def test_ukraine_is_not_a_preferred_region(self):
        cases = (
            region_candidate("Ukraine", "ukraine.cloudpath.live"),
            region_candidate("Ukraine 🇺🇦", "edge.example.net"),
            region_candidate("Kyiv", "ua.example.net"),
            region_candidate("Node", "example.ua"),
        )

        for item in cases:
            with self.subTest(item=item):
                self.assertEqual(2, region_score(item, ["us", "eu"]))


if __name__ == "__main__":
    unittest.main()
