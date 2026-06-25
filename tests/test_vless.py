import unittest

from proxy_xray.vless import candidate_fallback_score


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


class VlessScoreTest(unittest.TestCase):
    def test_recent_xray_selection_adds_score_reason(self):
        score, reasons = candidate_fallback_score(candidate(last_xray_selected_at=1990.0), now=2000.0)
        base_score, _base_reasons = candidate_fallback_score(candidate(), now=2000.0)

        self.assertGreater(score, base_score)
        self.assertTrue(any("xray-selected" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
