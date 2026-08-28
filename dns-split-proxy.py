#!/usr/bin/env python3
import argparse
import ipaddress
import json
import os
import socket
import socketserver
import struct
import threading
import time


RU_SUFFIXES = ("ru", "su", "xn--p1ai")


def normalize_qname(qname):
    return qname.rstrip(".").encode("idna").decode("ascii").lower()


def parse_qname(packet):
    labels = []
    pos = 12
    while pos < len(packet):
        length = packet[pos]
        if length == 0:
            break
        # Queries should not use compression, but avoid looping on malformed input.
        if length & 0xC0:
            break
        pos += 1
        labels.append(packet[pos : pos + length].decode("ascii", "ignore"))
        pos += length
    return ".".join(labels).lower().rstrip(".")


def split_upstream(value):
    if ":" in value and value.count(":") == 1:
        host, port = value.rsplit(":", 1)
        return host, int(port)
    return value, 53


def split_upstreams(value):
    upstreams = []
    for item in value.split(","):
        item = item.strip()
        if item:
            upstreams.append(split_upstream(item))
    if not upstreams:
        raise ValueError("at least one DNS upstream is required")
    return upstreams


def format_upstreams(upstreams):
    return ",".join(f"{host}:{port}" for host, port in upstreams)


def classify_qname(qname):
    qname = normalize_qname(qname)
    parts = qname.split(".")
    suffix = parts[-1] if parts else ""
    return "ru" if suffix in RU_SUFFIXES else "global"


def choose_upstreams(qname, ru_upstreams, global_upstreams):
    qclass = classify_qname(qname)
    return qclass, ru_upstreams if qclass == "ru" else global_upstreams


def recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("short socket response")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def socks_address(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna")
        if len(encoded) > 255:
            raise ValueError("SOCKS target hostname is too long")
        return b"\x03" + bytes([len(encoded)]) + encoded
    if address.version == 4:
        return b"\x01" + address.packed
    return b"\x04" + address.packed


def connect_socks(proxy_host, proxy_port, target, timeout):
    host, port = target
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        if recv_exact(sock, 2) != b"\x05\x00":
            raise OSError("SOCKS proxy rejected unauthenticated connection")
        sock.sendall(b"\x05\x01\x00" + socks_address(host) + struct.pack("!H", port))
        header = recv_exact(sock, 4)
        if header[0] != 5 or header[1] != 0:
            raise OSError(f"SOCKS CONNECT failed with code {header[1]}")
        address_type = header[3]
        if address_type == 1:
            recv_exact(sock, 4)
        elif address_type == 4:
            recv_exact(sock, 16)
        elif address_type == 3:
            recv_exact(sock, recv_exact(sock, 1)[0])
        else:
            raise OSError(f"SOCKS proxy returned invalid address type {address_type}")
        recv_exact(sock, 2)
        return sock
    except Exception:
        sock.close()
        raise


def connect_http(proxy_host, proxy_port, target, timeout):
    host, port = target
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        request = (
            f"CONNECT {authority} HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            "Proxy-Connection: keep-alive\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(1024)
            if not chunk:
                raise OSError("HTTP proxy closed CONNECT response")
            response.extend(chunk)
            if len(response) > 16384:
                raise OSError("HTTP proxy CONNECT response is too large")
        status_line = bytes(response).split(b"\r\n", 1)[0].split()
        if len(status_line) < 2 or not status_line[1].startswith(b"2"):
            status = status_line[1].decode("ascii", "replace") if len(status_line) >= 2 else "invalid"
            raise OSError(f"HTTP CONNECT failed with status {status}")
        return sock
    except Exception:
        sock.close()
        raise


def connect_upstream(upstream, timeout, route, proxy_host, socks_port, http_port):
    if route == "socks":
        return connect_socks(proxy_host, socks_port, upstream, timeout)
    if route == "http":
        return connect_http(proxy_host, http_port, upstream, timeout)
    return socket.create_connection(upstream, timeout=timeout)


def query_tcp(packet, upstream, timeout, route="direct", proxy_host="127.0.0.1", socks_port=1080, http_port=8123):
    host, port = upstream
    framed = struct.pack("!H", len(packet)) + packet
    with connect_upstream((host, port), timeout, route, proxy_host, socks_port, http_port) as sock:
        sock.settimeout(timeout)
        sock.sendall(framed)
        header = recv_exact(sock, 2)
        size = struct.unpack("!H", header)[0]
        return recv_exact(sock, size)


def positive_dns_response(packet):
    if len(packet) < 12:
        return False
    flags, answer_count = struct.unpack("!HH", packet[2:6])
    return flags & 0xF == 0 and answer_count > 0


def cache_key(packet):
    return packet[2:]


def response_for_query(response, packet):
    return packet[:2] + response[2:]


class ResponseCache:
    def __init__(self, fresh_ttl=300, stale_ttl=86400):
        self.fresh_ttl = max(0, fresh_ttl)
        self.stale_ttl = max(self.fresh_ttl, stale_ttl)
        self._items = {}
        self._lock = threading.Lock()

    def put(self, packet, response, now=None):
        if not positive_dns_response(response):
            return
        with self._lock:
            self._items[cache_key(packet)] = (now or time.time(), response)

    def get(self, packet, stale=False, now=None):
        now = now or time.time()
        limit = self.stale_ttl if stale else self.fresh_ttl
        with self._lock:
            item = self._items.get(cache_key(packet))
            if not item or now - item[0] > limit:
                return None
            return response_for_query(item[1], packet)


class BootstrapDomains:
    def __init__(self, path=None):
        self.path = path
        self._mtime = None
        self._domains = set()
        self._lock = threading.Lock()

    def domains(self):
        if not self.path:
            return set()
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            return set()
        with self._lock:
            if mtime != self._mtime:
                try:
                    with open(self.path, "r", encoding="utf-8") as handle:
                        self._domains = {
                            normalize_qname(line.strip())
                            for line in handle
                            if line.strip() and not line.lstrip().startswith("#")
                        }
                except OSError:
                    self._domains = set()
                self._mtime = mtime
            return set(self._domains)

    def contains(self, qname):
        qname = normalize_qname(qname)
        return any(qname == domain or qname.endswith(f".{domain}") for domain in self.domains())


def write_status(path, data):
    if not path:
        return
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class DNSHandler:
    ru_upstreams = []
    global_upstreams = []
    last_good = {"ru": 0, "global": 0}
    upstream_lock = threading.Lock()
    status_lock = threading.Lock()
    timeout = 5
    global_route = "direct"
    proxy_host = "127.0.0.1"
    socks_port = 1080
    http_port = 8123
    direct_fallback = False
    bootstrap = BootstrapDomains()
    cache = ResponseCache()
    status_file = None
    proxy_ready_file = None

    @classmethod
    def set_query_status(cls, **values):
        with cls.status_lock:
            write_status(
                cls.status_file,
                {
                    "time": time.time(),
                    "global_route": cls.global_route,
                    **values,
                },
            )

    @classmethod
    def resolve(cls, packet):
        qname = parse_qname(packet)
        qclass, upstreams = choose_upstreams(qname, cls.ru_upstreams, cls.global_upstreams)
        bootstrap = qclass == "global" and cls.bootstrap.contains(qname)
        route = "direct" if qclass == "ru" or bootstrap else cls.global_route
        route_label = route
        if (
            qclass == "global"
            and not bootstrap
            and route != "direct"
            and cls.proxy_ready_file
            and not os.path.exists(cls.proxy_ready_file)
        ):
            route = "direct"
            route_label = "direct-startup"
        cached = cls.cache.get(packet)
        if qclass == "global" and not bootstrap and cached:
            cls.set_query_status(status="ok", query_class=qclass, route="cache-fresh", bootstrap=False)
            return cached
        with cls.upstream_lock:
            start = cls.last_good.get(qclass, 0) % len(upstreams)
        ordered = upstreams[start:] + upstreams[:start]
        errors = []
        for offset, upstream in enumerate(ordered):
            try:
                response = query_tcp(
                    packet,
                    upstream,
                    cls.timeout,
                    route=route,
                    proxy_host=cls.proxy_host,
                    socks_port=cls.socks_port,
                    http_port=cls.http_port,
                )
                with cls.upstream_lock:
                    cls.last_good[qclass] = (start + offset) % len(upstreams)
                if qclass == "global" and not bootstrap:
                    cls.cache.put(packet, response)
                cls.set_query_status(
                    status="ok",
                    query_class=qclass,
                    route=route_label,
                    bootstrap=bootstrap,
                    upstream=f"{upstream[0]}:{upstream[1]}",
                )
                return response
            except Exception as exc:
                errors.append(f"{route_label} {upstream[0]}:{upstream[1]} {exc}")
        if qclass == "global" and route != "direct" and cls.direct_fallback:
            for upstream in ordered:
                try:
                    response = query_tcp(packet, upstream, cls.timeout, route="direct")
                    cls.cache.put(packet, response)
                    cls.set_query_status(
                        status="warn",
                        query_class=qclass,
                        route="direct-fallback",
                        bootstrap=False,
                        upstream=f"{upstream[0]}:{upstream[1]}",
                    )
                    return response
                except Exception as exc:
                    errors.append(f"direct fallback {upstream[0]}:{upstream[1]} {exc}")
        stale = cls.cache.get(packet, stale=True)
        if qclass == "global" and not bootstrap and stale:
            cls.set_query_status(status="warn", query_class=qclass, route="cache-stale", bootstrap=False)
            return stale
        cls.set_query_status(
            status="fail",
            query_class=qclass,
            route=route_label,
            bootstrap=bootstrap,
            error="; ".join(errors)[-1000:],
        )
        raise OSError(f"all {qclass} DNS upstreams failed: {'; '.join(errors)}")


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        packet, sock = self.request
        try:
            response = DNSHandler.resolve(packet)
            sock.sendto(response, self.client_address)
        except Exception:
            return


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            header = self.request.recv(2)
            if len(header) != 2:
                return
            size = struct.unpack("!H", header)[0]
            packet = recv_exact(self.request, size)
            response = DNSHandler.resolve(packet)
            self.request.sendall(struct.pack("!H", len(response)) + response)
        except Exception:
            return


class ThreadingUDPServer(socketserver.ThreadingMixIn, socketserver.UDPServer):
    daemon_threads = True
    allow_reuse_address = True


class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="Tiny split DNS relay over TCP upstreams")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=53)
    parser.add_argument("--dns-global", default="8.8.8.8", help="Comma-separated global DNS upstreams")
    parser.add_argument("--dns-ru", default="77.88.8.8", help="Comma-separated RU DNS upstreams")
    parser.add_argument("--global-route", choices=("direct", "socks", "http"), default="direct")
    parser.add_argument("--proxy-host", default="127.0.0.1")
    parser.add_argument("--proxy-socks-port", type=int, default=1080)
    parser.add_argument("--proxy-http-port", type=int, default=8123)
    parser.add_argument("--global-direct-fallback", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--bootstrap-file")
    parser.add_argument("--status-file")
    parser.add_argument("--proxy-ready-file")
    parser.add_argument("--cache-ttl", type=int, default=300)
    parser.add_argument("--stale-ttl", type=int, default=86400)
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--classify", help="Print selected upstream class for a domain and exit")
    args = parser.parse_args()

    if args.classify:
        print(classify_qname(args.classify))
        return

    DNSHandler.global_upstreams = split_upstreams(args.dns_global)
    DNSHandler.ru_upstreams = split_upstreams(args.dns_ru)
    DNSHandler.timeout = args.timeout
    DNSHandler.global_route = args.global_route
    DNSHandler.proxy_host = args.proxy_host
    DNSHandler.socks_port = args.proxy_socks_port
    DNSHandler.http_port = args.proxy_http_port
    DNSHandler.direct_fallback = args.global_direct_fallback
    DNSHandler.bootstrap = BootstrapDomains(args.bootstrap_file)
    DNSHandler.cache = ResponseCache(args.cache_ttl, args.stale_ttl)
    DNSHandler.status_file = args.status_file
    DNSHandler.proxy_ready_file = args.proxy_ready_file

    udp_server = ThreadingUDPServer((args.listen, args.port), UDPHandler)
    tcp_server = ThreadingTCPServer((args.listen, args.port), TCPHandler)
    threading.Thread(target=udp_server.serve_forever, daemon=True).start()
    print(
        f"[dns] split DNS listening on {args.listen}:{args.port}; "
        f"ru={format_upstreams(DNSHandler.ru_upstreams)} "
        f"global={format_upstreams(DNSHandler.global_upstreams)} "
        f"global-route={DNSHandler.global_route} "
        f"direct-fallback={DNSHandler.direct_fallback}",
        flush=True,
    )
    tcp_server.serve_forever()


if __name__ == "__main__":
    main()
