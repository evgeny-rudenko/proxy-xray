import unittest

from proxy_xray.subscription import parse_curl_headers, parse_subscription_usage


class SubscriptionUsageTest(unittest.TestCase):
    def test_parses_subscription_userinfo(self):
        usage = parse_subscription_usage(
            {
                "Subscription-Userinfo": "upload=10; download=90; total=1000; expire=1234567890",
            },
            fetched_at=100.0,
        )

        self.assertEqual(10, usage["upload_bytes"])
        self.assertEqual(90, usage["download_bytes"])
        self.assertEqual(100, usage["used_bytes"])
        self.assertEqual(900, usage["remaining_bytes"])
        self.assertEqual(10.0, usage["usage_percent"])
        self.assertEqual(1234567890, usage["expires_at"])
        self.assertEqual(100.0, usage["fetched_at"])

    def test_missing_or_invalid_quota_does_not_fail_subscription(self):
        self.assertIsNone(parse_subscription_usage({}))
        self.assertIsNone(parse_subscription_usage({"Subscription-Userinfo": "total=bad"}))
        self.assertIsNone(parse_subscription_usage({"Subscription-Userinfo": "total=0"}))

    def test_reads_final_response_headers_after_redirect(self):
        headers = parse_curl_headers(
            b"HTTP/1.1 302 Found\r\nLocation: /next\r\n\r\n"
            b"HTTP/2 200\r\nSubscription-Userinfo: upload=0; download=90; total=1000\r\n\r\n"
        )

        self.assertEqual("upload=0; download=90; total=1000", headers["subscription-userinfo"])


if __name__ == "__main__":
    unittest.main()
