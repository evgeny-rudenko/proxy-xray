#!/usr/bin/env python3
import argparse
import socket
import socketserver
import struct
import threading


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


def query_tcp(packet, upstream, timeout):
    host, port = upstream
    framed = struct.pack("!H", len(packet)) + packet
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(framed)
        header = sock.recv(2)
        if len(header) != 2:
            raise OSError("short DNS TCP response header")
        size = struct.unpack("!H", header)[0]
        chunks = []
        remaining = size
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise OSError("short DNS TCP response body")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("short DNS TCP request body")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class DNSHandler:
    ru_upstreams = []
    global_upstreams = []
    last_good = {"ru": 0, "global": 0}
    upstream_lock = threading.Lock()
    timeout = 5

    @classmethod
    def resolve(cls, packet):
        qname = parse_qname(packet)
        qclass, upstreams = choose_upstreams(qname, cls.ru_upstreams, cls.global_upstreams)
        with cls.upstream_lock:
            start = cls.last_good.get(qclass, 0) % len(upstreams)
        ordered = upstreams[start:] + upstreams[:start]
        errors = []
        for offset, upstream in enumerate(ordered):
            try:
                response = query_tcp(packet, upstream, cls.timeout)
                with cls.upstream_lock:
                    cls.last_good[qclass] = (start + offset) % len(upstreams)
                return response
            except Exception as exc:
                errors.append(f"{upstream[0]}:{upstream[1]} {exc}")
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
    parser.add_argument("--timeout", type=int, default=5)
    parser.add_argument("--classify", help="Print selected upstream class for a domain and exit")
    args = parser.parse_args()

    if args.classify:
        print(classify_qname(args.classify))
        return

    DNSHandler.global_upstreams = split_upstreams(args.dns_global)
    DNSHandler.ru_upstreams = split_upstreams(args.dns_ru)
    DNSHandler.timeout = args.timeout

    udp_server = ThreadingUDPServer((args.listen, args.port), UDPHandler)
    tcp_server = ThreadingTCPServer((args.listen, args.port), TCPHandler)
    threading.Thread(target=udp_server.serve_forever, daemon=True).start()
    print(
        f"[dns] split DNS listening on {args.listen}:{args.port}; "
        f"ru={format_upstreams(DNSHandler.ru_upstreams)} "
        f"global={format_upstreams(DNSHandler.global_upstreams)}",
        flush=True,
    )
    tcp_server.serve_forever()


if __name__ == "__main__":
    main()
