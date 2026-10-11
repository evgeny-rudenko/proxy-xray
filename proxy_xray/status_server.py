from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import urlsplit

from .diagnostics import build_diagnostics
from .status import LOG_BUFFER, enqueue_control_command, log, status_snapshot
from .view_helpers import format_time, event_timeline_rows, recent_log_text, subscription_quota_badge
from .view_pages import render_client_html, render_diagnostics_html, render_servers_html, client_connection_url, host_from_header
from .dashboard_view import dashboard_v5_fragments, render_dashboard_v5_html
from .routes import POLICIES

STATUS_ARGS = None


class StatusHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_bytes(self, data, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_download(self, data, content_type, filename):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def do_POST(self):
        if self.path == "/control/route-policy":
            origin = self.headers.get("Origin")
            try:
                parsed = urlsplit(origin or "")
            except ValueError:
                self.send_error(403, "invalid Origin")
                return
            if origin and (parsed.scheme != "http" or parsed.netloc.lower() != self.headers.get("Host", "").lower()
                           or parsed.path or parsed.query or parsed.fragment):
                self.send_error(403, "foreign Origin is not allowed")
                return
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self.send_error(403, "cross-site request is not allowed")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 or self.headers.get("Transfer-Encoding"):
                    raise ValueError("invalid request size")
                if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise ValueError("JSON is required")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != {"policy"} or payload["policy"] not in POLICIES:
                    raise ValueError("invalid routing policy")
            except (ValueError, UnicodeDecodeError):
                self.send_error(400, "invalid routing policy request")
                return
            command = enqueue_control_command("route_policy", payload)
            data = json.dumps({"id": command["id"], "status": command.get("status", "queued")}).encode()
            self.send_response(202)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/control/recheck-route":
            enqueue_control_command("recheck_route")
            self.send_redirect("/")
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        args = STATUS_ARGS
        if self.path in ("/", "/status", "/dashboard-v5", "/v5"):
            self.send_bytes(render_dashboard_v5_html(), "text/html; charset=utf-8")
            return
        if self.path == "/fragments/dashboard-v5":
            data = json.dumps(
                {"time": time.time(), "fragments": dashboard_v5_fragments()},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_bytes(data, "application/json; charset=utf-8")
            return
        if self.path == "/client":
            self.send_bytes(render_client_html(args, self.headers.get("Host")), "text/html; charset=utf-8")
            return
        if self.path == "/diagnostics":
            self.send_bytes(render_diagnostics_html(args), "text/html; charset=utf-8")
            return
        if self.path == "/diagnostics/bundle":
            data = json.dumps(build_diagnostics(args), ensure_ascii=False, indent=2).encode("utf-8")
            self.send_download(data, "application/json; charset=utf-8", "proxy-xray-diagnostics.json")
            return
        if self.path == "/diagnostics.json":
            data = json.dumps(build_diagnostics(args), ensure_ascii=False, indent=2).encode("utf-8")
            self.send_bytes(data, "application/json; charset=utf-8")
            return
        if self.path == "/servers/live":
            self.send_bytes(render_servers_html("live"), "text/html; charset=utf-8")
            return
        if self.path == "/servers/all":
            self.send_bytes(render_servers_html("all"), "text/html; charset=utf-8")
            return
        if self.path == "/json":
            data = json.dumps(status_snapshot(), ensure_ascii=False, indent=2).encode("utf-8")
            self.send_bytes(data, "application/json; charset=utf-8")
            return
        if self.path == "/logs":
            text = "\n".join(f"{format_time(item.get('time'))} {item.get('line', '')}" for item in LOG_BUFFER)
            self.send_bytes(text.encode("utf-8"), "text/plain; charset=utf-8")
            return
        self.send_response(404)
        self.end_headers()


def start_status_server(args):
    global STATUS_ARGS
    STATUS_ARGS = args
    if args.status_port <= 0:
        return None
    server = ThreadingHTTPServer((args.status_listen, args.status_port), StatusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log(f"status web server listening on {args.status_listen}:{args.status_port}")
    return server
