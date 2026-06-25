import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from proxy_xray.state import load_state, persist_state


def candidate(uri="vless://candidate", last_ok_at=None, last_fail_at=None, latency=None):
    return {
        "uri": uri,
        "name": "candidate",
        "host": "candidate.example.com",
        "port": 443,
        "source": "subscription",
        "tag": "proxy-sub-0",
        "last_latency": latency,
        "last_throughput_kbps": None,
        "last_ok_at": last_ok_at,
        "last_fail_at": last_fail_at,
        "quarantine_until": None,
        "quarantine_reason": None,
    }


class StateTest(unittest.TestCase):
    def test_corrupt_state_is_moved_aside_and_empty_state_is_returned(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            with open(state_path, "w", encoding="utf-8") as handle:
                handle.write("{not-json")

            state = load_state(state_path)

            self.assertEqual(2, state["schema_version"])
            self.assertEqual({}, state["candidates"])
            self.assertFalse(os.path.exists(state_path))
            backups = [name for name in os.listdir(tmpdir) if name.startswith("state.json.corrupt.")]
            self.assertEqual(1, len(backups))

    def test_legacy_state_is_normalized_to_schema_v2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            with open(state_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "last_selected_uri": "vless://candidate",
                        "candidates": {
                            "vless://candidate": {
                                "last_ok_at": 100.0,
                                "last_latency": 0.2,
                            }
                        },
                    },
                    handle,
                )

            state = load_state(state_path)

            record = state["candidates"]["vless://candidate"]
            self.assertEqual(2, state["schema_version"])
            self.assertEqual([], record["recent_checks"])
            self.assertEqual(0, record["quality"]["checks"])

    def test_persist_state_writes_recent_checks_and_quality(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = os.path.join(tmpdir, "state.json")
            args = SimpleNamespace(state_file=state_path)
            first = candidate(last_ok_at=100.0, latency=0.2)
            second = candidate(last_fail_at=200.0)

            persist_state(args, [first], active=first)
            persist_state(args, [second], active=second)

            state = load_state(state_path)
            record = state["candidates"]["vless://candidate"]

            self.assertEqual(2, state["schema_version"])
            self.assertEqual(2, len(record["recent_checks"]))
            self.assertEqual(2, record["quality"]["checks"])
            self.assertEqual(1, record["quality"]["successes"])
            self.assertEqual(1, record["quality"]["failures"])
            self.assertEqual(1, record["quality"]["consecutive_failures"])
            self.assertEqual(0.5, record["quality"]["success_rate"])


if __name__ == "__main__":
    unittest.main()
