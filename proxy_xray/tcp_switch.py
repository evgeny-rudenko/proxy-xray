import socket
import threading

from .status import log


class TcpSwitch:
    def __init__(self, name, listen_host, listen_port, target_host="127.0.0.1", target_port=None):
        self.name = name
        self.listen_host = listen_host
        self.listen_port = listen_port
        self._target = (target_host, target_port)
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self._server = None
        self._thread = None

    def set_target(self, target_host, target_port):
        with self._lock:
            self._target = (target_host, target_port)
        log(f"{self.name} switch target -> {target_host}:{target_port}")

    def target(self):
        with self._lock:
            return self._target

    def start(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((self.listen_host, self.listen_port))
        self._server.listen(200)
        self._server.settimeout(1.0)
        self._thread = threading.Thread(target=self._accept_loop, name=f"tcp-switch-{self.name}", daemon=True)
        self._thread.start()
        host = self.listen_host or "0.0.0.0"
        target_host, target_port = self.target()
        log(f"{self.name} switch listening on {host}:{self.listen_port} -> {target_host}:{target_port}")

    def stop(self):
        self._stopping.set()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)

    def _accept_loop(self):
        while not self._stopping.is_set():
            try:
                client, _addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self._handle_client, args=(client,), daemon=True)
            thread.start()

    def _shutdown(self, sock, how):
        try:
            sock.shutdown(how)
        except OSError:
            pass

    def _pipe(self, source, target):
        try:
            while not self._stopping.is_set():
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except (OSError, TimeoutError):
            pass
        finally:
            self._shutdown(target, socket.SHUT_WR)

    def _handle_client(self, client):
        target_host, target_port = self.target()
        if not target_port:
            client.close()
            return
        try:
            upstream = socket.create_connection((target_host, target_port), timeout=5)
        except OSError:
            client.close()
            return
        with client, upstream:
            for sock in (client, upstream):
                sock.settimeout(300)
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass

            client_to_upstream = threading.Thread(
                target=self._pipe,
                args=(client, upstream),
                name=f"tcp-switch-{self.name}-up",
                daemon=True,
            )
            upstream_to_client = threading.Thread(
                target=self._pipe,
                args=(upstream, client),
                name=f"tcp-switch-{self.name}-down",
                daemon=True,
            )
            client_to_upstream.start()
            upstream_to_client.start()
            client_to_upstream.join()
            upstream_to_client.join()


def start_switches(active_slot, args):
    switches = {
        "socks": TcpSwitch("SOCKS", "0.0.0.0", 1080, target_port=active_slot["socks_port"]),
        "http": TcpSwitch("HTTP", "0.0.0.0", 8123, target_port=active_slot["http_port"]),
    }
    if args.inbound_vless:
        switches["vless"] = TcpSwitch(
            "LAN VLESS",
            args.inbound_vless_listen,
            args.inbound_vless_port,
            target_port=active_slot["vless_port"],
        )
    for switch in switches.values():
        switch.start()
    return switches


def set_switch_targets(switches, active_slot):
    if "socks" in switches:
        switches["socks"].set_target("127.0.0.1", active_slot["socks_port"])
    if "http" in switches:
        switches["http"].set_target("127.0.0.1", active_slot["http_port"])
    if "vless" in switches:
        switches["vless"].set_target("127.0.0.1", active_slot["vless_port"])


def stop_switches(switches):
    for switch in switches.values():
        switch.stop()
