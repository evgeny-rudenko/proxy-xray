import argparse
import os

from .status import log
from .status_server import start_status_server
from .supervisor import run


def build_parser():
    parser = argparse.ArgumentParser(description="Subscription supervisor for proxy-xray")
    parser.add_argument("--sub-url", required=True)
    parser.add_argument("--extra-file")
    parser.add_argument("--extra-vless", action="append", default=[])
    parser.add_argument("--dns", default="1.1.1.1")
    parser.add_argument("--rules-file")
    parser.add_argument("--inject-file")
    parser.add_argument("--prefer", default="us,eu")
    parser.add_argument("--exclude", default="ru,russia,россия")
    parser.add_argument("--health-url", default="https://www.gstatic.com/generate_204")
    parser.add_argument("--check-interval", type=int, default=60)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--refresh-interval", type=int, default=86400)
    parser.add_argument("--retry-interval", type=int, default=300)
    parser.add_argument("--state-file", default="/var/lib/proxy-xray/state.json")
    parser.add_argument("--health-timeout", type=int, default=10)
    parser.add_argument("--degrade-latency", type=float, default=6.0)
    parser.add_argument("--degrade-checks", type=int, default=3)
    parser.add_argument("--fetch-timeout", type=int, default=20)
    parser.add_argument("--sub-fetch-mode", choices=("direct", "proxy", "auto"), default="auto")
    parser.add_argument("--sub-fetch-proxy", default="socks5h://127.0.0.1:1080")
    parser.add_argument("--sub-post-start-refresh-delay", type=int, default=15)
    parser.add_argument("--observatory-probe-interval", default="10s")
    parser.add_argument("--balancer-strategy", default="leastPing")
    parser.add_argument("--active-pool-size", type=int, default=1)
    parser.add_argument("--standby-pool-size", type=int, default=1)
    parser.add_argument("--throughput-check-interval", type=int, default=300)
    parser.add_argument("--throughput-url", default="https://speed.cloudflare.com/__down?bytes=2000000")
    parser.add_argument("--throughput-min-kbps", type=float, default=1500)
    parser.add_argument("--throughput-max-time", type=int, default=20)
    parser.add_argument("--throughput-degrade-checks", type=int, default=3)
    parser.add_argument("--standby-max-age", type=int, default=600)
    parser.add_argument("--failover-cooldown", type=int, default=180)
    parser.add_argument("--hot-standby-fast-failures", type=int, default=1)
    parser.add_argument("--quarantine-duration", type=int, default=900)
    parser.add_argument("--candidate-check-min-interval", type=int, default=120)
    parser.add_argument("--candidate-check-max-interval", type=int, default=300)
    parser.add_argument("--candidate-check-timeout", type=int, default=10)
    parser.add_argument("--candidate-check-start-delay", type=float, default=0.7)
    parser.add_argument("--candidate-check-extra-weight", type=int, default=5)
    parser.add_argument("--diagnostics-timeout", type=int, default=4)
    parser.add_argument("--active-path-interval", type=int, default=15)
    parser.add_argument("--asset-dir", default="/opt/proxy-xray/assets")
    parser.add_argument("--asset-refresh-interval", type=int, default=86400)
    parser.add_argument("--asset-fetch-timeout", type=int, default=30)
    parser.add_argument("--asset-refresh-on-start", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--inbound-vless", action="store_true")
    parser.add_argument("--inbound-vless-port", type=int, default=10086)
    parser.add_argument("--inbound-vless-id")
    parser.add_argument("--inbound-vless-listen", default="0.0.0.0")
    parser.add_argument("--telegram-bot-token", default=os.environ.get("TELEGRAM_BOT_TOKEN"))
    parser.add_argument("--telegram-chat-id", default=os.environ.get("TELEGRAM_CHAT_ID"))
    parser.add_argument("--telegram-timeout", type=int, default=10)
    parser.add_argument("--post-switch-notify-delay", type=float, default=2.0)
    parser.add_argument("--status-listen", default="0.0.0.0")
    parser.add_argument("--status-port", type=int, default=18080)
    parser.add_argument("--debug", action="store_true")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.inbound_vless and not args.inbound_vless_id:
        log("fatal: --inbound-vless-id is required when --inbound-vless is enabled")
        return 1

    try:
        start_status_server(args)
        run(args)
    except Exception as exc:
        log(f"fatal: {exc}")
        return 1
    return 0
