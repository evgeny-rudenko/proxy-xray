import argparse
import os

from .status import log
from .status_server import start_status_server
from .supervisor import run


def build_parser():
    parser = argparse.ArgumentParser(description="Amsterdam exit with subscription relay and emergency exit")
    parser.add_argument("--sub-url", required=True)
    parser.add_argument("--extra-file", required=True)
    parser.add_argument("--extra-vless", action="append", default=[])
    parser.add_argument("--dns-global", default="8.8.8.8,1.1.1.1")
    parser.add_argument("--dns-ru", default="77.88.8.8,77.88.8.1")
    parser.add_argument("--direct-ru", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dns-global-route", choices=("socks",), default="socks")
    parser.set_defaults(dns_global_direct_fallback=False)
    parser.add_argument("--dns-bootstrap-file", default="/tmp/proxy-xray-dns-bootstrap.txt")
    parser.add_argument("--dns-status-file", default="/tmp/proxy-xray-dns-status.json")
    parser.add_argument("--dns-proxy-ready-file", default="/tmp/proxy-xray-dns-proxy-ready")
    parser.add_argument("--prefer", default="us,eu")
    parser.add_argument("--exclude", default="ru,russia,russian,россия,москва,moscow")
    parser.add_argument("--health-url", default="https://www.gstatic.com/generate_204")
    parser.add_argument("--health-backup-url", default="https://cp.cloudflare.com/generate_204")
    parser.add_argument("--check-interval", type=int, default=20)
    parser.add_argument("--max-failures", type=int, default=2)
    parser.add_argument("--return-successes", type=int, default=3)
    parser.add_argument("--return-hold", type=int, default=180)
    parser.add_argument("--direct-return-stable", type=int, default=300)
    parser.add_argument("--direct-retry-delay", type=int, default=600)
    parser.add_argument("--direct-retry-max", type=int, default=900)
    parser.add_argument("--direct-failure-window", type=int, default=180)
    parser.add_argument("--relay-pool-size", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--refresh-interval", type=int, default=7200)
    parser.add_argument("--state-file", default="/var/lib/proxy-xray/state.json")
    parser.add_argument("--health-timeout", type=int, default=5)
    parser.add_argument("--fetch-timeout", type=int, default=20)
    parser.add_argument("--sub-fetch-mode", choices=("direct", "proxy", "auto"), default="auto")
    parser.add_argument("--sub-fetch-proxy", default="socks5h://127.0.0.1:1080")
    parser.add_argument("--sub-post-start-refresh-delay", type=int, default=30)
    parser.add_argument("--observatory-probe-interval", default="10s")
    parser.add_argument("--throughput-check-interval", type=int, default=300)
    parser.add_argument("--throughput-url", default="https://speed.cloudflare.com/__down?bytes=2000000")
    parser.add_argument("--throughput-min-kbps", type=float, default=1500)
    parser.add_argument("--throughput-max-time", type=int, default=20)
    parser.add_argument("--quality-check-interval", type=int, default=60)
    parser.add_argument("--quality-url", default="https://speed.cloudflare.com/__down?bytes=512000")
    parser.add_argument("--quality-min-kbps", type=float, default=1000)
    parser.add_argument("--quality-max-time", type=int, default=10)
    parser.add_argument("--candidate-check-min-interval", type=int, default=120)
    parser.add_argument("--candidate-check-max-interval", type=int, default=300)
    parser.add_argument("--candidate-check-start-delay", type=float, default=0.7)
    parser.add_argument("--diagnostics-timeout", type=int, default=4)
    parser.add_argument("--diagnostic-url", action="append", default=[])
    parser.add_argument("--diagnostic-probe-timeout", type=int, default=6)
    parser.add_argument("--active-path-interval", type=int, default=15)
    parser.add_argument("--asset-dir", default="/opt/proxy-xray/assets")
    parser.add_argument("--asset-refresh-interval", type=int, default=86400)
    parser.add_argument("--asset-fetch-timeout", type=int, default=30)
    parser.add_argument("--asset-refresh-on-start", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--inbound-vless", action="store_true")
    parser.add_argument("--inbound-vless-port", type=int, default=10086)
    parser.add_argument("--inbound-vless-id")
    parser.add_argument("--inbound-vless-listen", default="0.0.0.0")
    parser.add_argument("--telegram-bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    parser.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"))
    parser.add_argument("--telegram-timeout", type=int, default=10)
    parser.add_argument("--status-listen", default="0.0.0.0")
    parser.add_argument("--status-port", type=int, default=18080)
    parser.add_argument("--debug", action="store_true")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.inbound_vless and not args.inbound_vless_id:
        parser.error("--inbound-vless-id is required with --inbound-vless")
    for name in ("check_interval", "max_failures", "return_successes", "health_timeout",
                 "refresh_interval", "active_path_interval", "candidate_check_min_interval",
                 "direct_return_stable", "direct_retry_delay", "direct_retry_max", "direct_failure_window"):
        if getattr(args, name) <= 0:
            parser.error(name.replace("_", "-") + " must be positive")
    if args.candidate_check_max_interval < args.candidate_check_min_interval or args.return_hold < 0:
        parser.error("invalid candidate interval or return hold")
    if args.direct_retry_max < args.direct_retry_delay:
        parser.error("direct-retry-max must be at least direct-retry-delay")
    server = start_status_server(args)
    try:
        run(args)
    except Exception as exc:
        log(f"fatal: {type(exc).__name__}; see route status for the last verified path")
        return 1
    finally:
        if server:
            server.shutdown()
            server.server_close()
    return 0
