import unittest

from proxy_xray.xray_api import candidate_by_runtime_tag, parse_balancer_selected_tag, parse_balancer_select_tags


class XrayApiTest(unittest.TestCase):
    def test_parse_balancer_selects_from_xray_api_output(self):
        output = """
  - Selecting Override:
    1
  - Selects:
    1   proxy-sub-0
"""

        self.assertEqual(["proxy-sub-0"], parse_balancer_select_tags(output))
        self.assertEqual("proxy-sub-0", parse_balancer_selected_tag(output))

    def test_parse_balancer_selected_tag_from_text_fallback(self):
        self.assertEqual("proxy-extra-1", parse_balancer_selected_tag('selected: "proxy-extra-1"'))

    def test_runtime_tag_uses_pool_order_instead_of_persistent_candidate_tag(self):
        candidates = [
            {"tag": "proxy-extra-3", "source": "extra", "name": "first in runtime config"},
            {"tag": "proxy-extra-1", "source": "extra", "name": "second in runtime config"},
        ]

        selected = candidate_by_runtime_tag(candidates, "proxy-extra-0")

        self.assertEqual("first in runtime config", selected["name"])


if __name__ == "__main__":
    unittest.main()
