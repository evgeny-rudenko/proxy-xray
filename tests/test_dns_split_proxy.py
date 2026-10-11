import importlib.util
import os
import struct
import tempfile
import unittest
from unittest import mock

from proxy_xray.dns_runtime import bootstrap_domains, write_bootstrap_domains


ROOT = os.path.dirname(os.path.dirname(__file__))
SPEC = importlib.util.spec_from_file_location("dns_split_proxy", os.path.join(ROOT, "dns-split-proxy.py"))
DNS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DNS)


def query_packet(name, query_id=1):
    labels = b"".join(bytes([len(part)]) + part.encode("ascii") for part in name.split(".")) + b"\0"
    return struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + labels + struct.pack("!HH", 1, 1)


def positive_response(packet):
    return packet[:2] + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0) + packet[12:]


class Args:
    sub_url = "https://subscription.example/sub/key"
    dns_bootstrap_file = None


class DnsSplitProxyTests(unittest.TestCase):
    def setUp(self):
        DNS.DNSHandler.ru_upstreams = [("77.88.8.8", 53)]
        DNS.DNSHandler.global_upstreams = [("8.8.8.8", 53), ("1.1.1.1", 53)]
        DNS.DNSHandler.global_route = "socks"
        DNS.DNSHandler.proxy_host = "127.0.0.1"
        DNS.DNSHandler.socks_port = 1080
        DNS.DNSHandler.http_port = 8123
        DNS.DNSHandler.direct_fallback = False
        DNS.DNSHandler.bootstrap = DNS.BootstrapDomains()
        DNS.DNSHandler.cache = DNS.ResponseCache(fresh_ttl=300, stale_ttl=3600)
        DNS.DNSHandler.status_file = None
        DNS.DNSHandler.proxy_ready_file = None
        DNS.DNSHandler.last_good = {"ru": 0, "global": 0}

    def test_global_query_uses_configured_vless_transport(self):
        packet = query_packet("youtube.com")
        with mock.patch.object(DNS, "query_tcp", return_value=positive_response(packet)) as query:
            DNS.DNSHandler.resolve(packet)
        self.assertEqual(query.call_args.kwargs["route"], "socks")

    def test_ru_and_bootstrap_queries_remain_direct(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("edge.example\n")
            bootstrap_path = handle.name
        self.addCleanup(lambda: os.path.exists(bootstrap_path) and os.unlink(bootstrap_path))
        DNS.DNSHandler.bootstrap = DNS.BootstrapDomains(bootstrap_path)

        routes = []

        def fake_query(packet, _upstream, _timeout, **kwargs):
            routes.append(kwargs["route"])
            return positive_response(packet)

        with mock.patch.object(DNS, "query_tcp", side_effect=fake_query):
            DNS.DNSHandler.resolve(query_packet("yandex.ru", 2))
            DNS.DNSHandler.resolve(query_packet("edge.example", 3))
        self.assertEqual(routes, ["direct", "direct"])

    def test_global_query_is_blocked_before_proxy_is_ready(self):
        packet = query_packet("redirected-subscription.example")
        DNS.DNSHandler.proxy_ready_file = "/path/that/does/not/exist"
        with mock.patch.object(DNS, "query_tcp", return_value=positive_response(packet)) as query:
            with self.assertRaises(OSError):
                DNS.DNSHandler.resolve(packet)
        query.assert_not_called()

    def test_foreign_dns_failure_never_falls_back_direct(self):
        with mock.patch.object(DNS, "query_tcp", side_effect=OSError("offline")) as query:
            with self.assertRaises(OSError):
                DNS.DNSHandler.resolve(query_packet("youtube.com"))
        self.assertTrue(all(call.kwargs["route"] == "socks" for call in query.call_args_list))

    def test_positive_global_response_is_served_from_cache(self):
        first = query_packet("github.com", 10)
        second = query_packet("github.com", 11)
        with mock.patch.object(DNS, "query_tcp", return_value=positive_response(first)) as query:
            DNS.DNSHandler.resolve(first)
            cached = DNS.DNSHandler.resolve(second)
        self.assertEqual(query.call_count, 1)
        self.assertEqual(cached[:2], second[:2])

    def test_bootstrap_domain_file_contains_subscription_and_candidate_hosts(self):
        args = Args()
        with tempfile.TemporaryDirectory() as directory:
            args.dns_bootstrap_file = os.path.join(directory, "bootstrap.txt")
            candidates = [
                {"host": "edge-one.example"},
                {"host": "203.0.113.10"},
                {"host": "EDGE-TWO.EXAMPLE"},
            ]
            written = write_bootstrap_domains(args, candidates)
            with open(args.dns_bootstrap_file, "r", encoding="utf-8") as handle:
                saved = handle.read().splitlines()
        self.assertEqual(
            written,
            ["edge-one.example", "edge-two.example", "subscription.example"],
        )
        self.assertEqual(saved, written)
        self.assertEqual(bootstrap_domains(args, candidates), written)


if __name__ == "__main__":
    unittest.main()
