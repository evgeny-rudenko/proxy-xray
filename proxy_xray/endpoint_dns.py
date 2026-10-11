"""Bootstrap endpoint resolution never depends on the selected Amsterdam exit."""
import importlib.util
import ipaddress
from pathlib import Path
import secrets
import socket
import struct
import time

from .route_probe import pinned_session

_spec = importlib.util.spec_from_file_location("endpoint_dns", Path(__file__).resolve().parent.parent / "dns-split-proxy.py")
_dns = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_dns)


def skip_name(packet, offset):
    while offset < len(packet):
        size = packet[offset]
        offset += 1
        if size & 0xC0 == 0xC0:
            return offset + 1
        if not size:
            return offset
        offset += size
    raise ValueError("truncated DNS name")


def parse_addresses(packet, query_id):
    if len(packet) < 12:
        return []
    ident, flags, questions, answers = struct.unpack("!HHHH", packet[:8])
    if ident != query_id or not flags & 0x8000 or flags & 15:
        return []
    offset = 12
    for _ in range(questions):
        offset = skip_name(packet, offset) + 4
    result = []
    for _ in range(answers):
        offset = skip_name(packet, offset)
        kind, klass, ttl, size = struct.unpack("!HHIH", packet[offset:offset + 10])
        offset += 10
        data = packet[offset:offset + size]
        offset += size
        if kind == 1 and klass == 1 and len(data) == 4:
            result.append((str(ipaddress.ip_address(data)), min(3600, max(30, ttl))))
    return result


class EndpointResolver:
    def __init__(self, args):
        self.args = args
        self.cache = {}

    def resolve(self, candidate, relay=None):
        host = candidate["host"]
        cache_key = (host, relay["uri"] if relay is not None else "bootstrap")
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            pass
        cached = self.cache.get(cache_key)
        if cached and cached[1] > time.monotonic():
            self.apply(candidate, cached[0])
            return True
        ident = secrets.randbelow(65536)
        labels = host.encode("idna").split(b".")
        packet = struct.pack("!6H", ident, 0x100, 1, 0, 0, 0)
        packet += b"".join(bytes([len(label)]) + label for label in labels) + b"\x00\x00\x01\x00\x01"
        def query(port=None):
            upstreams = self.args.dns_global if port is not None else self.args.dns_ru
            for upstream in _dns.split_upstreams(upstreams):
                try:
                    response = _dns.query_tcp(packet, upstream, min(3, self.args.health_timeout),
                                              route="socks" if port else "direct", socks_port=port or 1080)
                    addresses = parse_addresses(response, ident)
                    if addresses:
                        return addresses[0]
                except (OSError, ValueError, struct.error):
                    continue
            return None
        address = None
        if relay is not None:
            with pinned_session(relay, self.args) as (port, proc):
                if proc.poll() is None:
                    address = query(port)
        else:
            address = query()
        if address:
            self.cache[cache_key] = (address[0], time.monotonic() + address[1])
            self.apply(candidate, address[0])
            return True
        # A stale endpoint IP may still be usable; never use stale public-DNS data as a new route proof.
        if cached:
            self.apply(candidate, cached[0])
            return True
        return False

    @staticmethod
    def apply(candidate, address):
        stream = candidate["outbound"].get("streamSettings", {})
        if stream.get("security") == "tls":
            stream.setdefault("tlsSettings", {}).setdefault("serverName", candidate["host"])
        elif stream.get("security") == "reality":
            stream.setdefault("realitySettings", {}).setdefault("serverName", candidate["host"])
        candidate["outbound"]["settings"]["vnext"][0]["address"] = address

