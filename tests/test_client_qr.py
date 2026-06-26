import unittest
from types import SimpleNamespace

from proxy_xray.qr import qr_svg
from proxy_xray.status_server import client_connection_url, host_from_header


class ClientQrTest(unittest.TestCase):
    def test_host_from_header_strips_status_port(self):
        self.assertEqual("192.168.2.200", host_from_header("192.168.2.200:18080"))
        self.assertEqual("proxy.local", host_from_header("proxy.local"))
        self.assertEqual("2001:db8::1", host_from_header("[2001:db8::1]:18080"))

    def test_client_connection_url_uses_inbound_port_and_uuid(self):
        args = SimpleNamespace(inbound_vless_id="11111111-1111-4111-8111-111111111111", inbound_vless_port=10086)

        url = client_connection_url(args, "192.168.2.200")

        self.assertEqual(
            "vless://11111111-1111-4111-8111-111111111111@192.168.2.200:10086?security=none&type=tcp#home-proxy",
            url,
        )

    def test_qr_svg_renders_connection_string(self):
        url = "vless://11111111-1111-4111-8111-111111111111@192.168.2.200:10086?security=none&type=tcp#home-proxy"

        svg = qr_svg(url)

        self.assertIn("<svg", svg)
        self.assertIn("<rect", svg)
        self.assertIn("VLESS connection QR code", svg)


if __name__ == "__main__":
    unittest.main()
