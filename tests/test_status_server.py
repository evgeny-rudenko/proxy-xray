import unittest

from proxy_xray.status import enqueue_control_command, pop_control_commands, status_snapshot
from proxy_xray.status_server import event_timeline_rows, recent_log_text


class StatusServerLogOrderTest(unittest.TestCase):
    def tearDown(self):
        pop_control_commands()

    def test_web_log_panels_show_newest_events_first(self):
        logs = [
            {"time": 1, "line": "old event"},
            {"time": 2, "line": "middle event"},
            {"time": 3, "line": "new event"},
        ]

        text = recent_log_text(logs)
        self.assertLess(text.index("new event"), text.index("middle event"))
        self.assertLess(text.index("middle event"), text.index("old event"))

        html = event_timeline_rows(logs)
        self.assertLess(html.index("new event"), html.index("middle event"))
        self.assertLess(html.index("middle event"), html.index("old event"))

    def test_control_command_is_queued_once_for_supervisor(self):
        command = enqueue_control_command("force_extra_pool")

        self.assertEqual("queued", status_snapshot()["last_control_command"]["status"])
        self.assertEqual([command], pop_control_commands())
        self.assertEqual([], pop_control_commands())


if __name__ == "__main__":
    unittest.main()
