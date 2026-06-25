import unittest
from types import SimpleNamespace

from proxy_xray.diagnostics import diagnostic_urls, redact_value


class DiagnosticsTest(unittest.TestCase):
    def test_redact_value_masks_common_secret_shapes(self):
        data = {
            "subscription": "https://example.com/sub/secretToken123",
            "vless": "vless://00000000-0000-0000-0000-000000000000@example.com:443#name",
            "telegram": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_123456",
            "uuid": "11111111-2222-3333-4444-555555555555",
        }

        redacted = redact_value(data)
        joined = " ".join(redacted.values())

        self.assertNotIn("secretToken123", joined)
        self.assertNotIn("vless://", joined)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ", joined)
        self.assertNotIn("11111111-2222-3333-4444-555555555555", joined)
        self.assertGreaterEqual(joined.count("<redacted>"), 4)

    def test_diagnostic_urls_accept_repeated_and_csv_values(self):
        args = SimpleNamespace(diagnostic_url=["https://a.example/, https://b.example/", "https://c.example/"])

        self.assertEqual(
            ["https://a.example/", "https://b.example/", "https://c.example/"],
            diagnostic_urls(args),
        )

    def test_diagnostic_urls_default_to_operational_probes(self):
        args = SimpleNamespace(
            diagnostic_url=[],
            health_url="https://health.example/generate_204",
            quality_url="https://speed.example/down",
        )

        urls = diagnostic_urls(args)

        self.assertEqual("https://health.example/generate_204", urls[0])
        self.assertEqual("https://speed.example/down", urls[1])
        self.assertIn("https://pikabu.ru/", urls)


if __name__ == "__main__":
    unittest.main()
