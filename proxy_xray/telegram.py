import subprocess

from .status import log


def send_telegram_notification(args, text):
    if not args.telegram_bot_token:
        return
    if not args.telegram_chat_id:
        log("telegram notification skipped: --telegram-chat-id is not set")
        return

    url = f"https://api.telegram.org/bot{args.telegram_bot_token}/sendMessage"
    command = [
        "curl",
        "-o",
        "/dev/null",
        "-sS",
        "--max-time",
        str(args.telegram_timeout),
        "-x",
        "socks5h://127.0.0.1:1080",
        "-X",
        "POST",
        url,
        "-d",
        f"chat_id={args.telegram_chat_id}",
        "--data-urlencode",
        f"text={text}",
    ]
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=args.telegram_timeout + 2,
        )
    except subprocess.TimeoutExpired:
        log("telegram notification failed: timeout")
        return
    if result.returncode == 0:
        log("telegram notification sent")
    else:
        log("telegram notification failed")


def candidate_description(candidate):
    if not candidate:
        return "unknown"
    stream = candidate["outbound"].get("streamSettings", {})
    network = stream.get("network", "unknown")
    security = stream.get("security", "unknown")
    source = candidate.get("source", "unknown")
    return (
        f"{candidate['name']}\n"
        f"  endpoint: {candidate['host']}:{candidate['port']}\n"
        f"  transport: {network}/{security}\n"
        f"  source: {source}"
    )


def native_recovery_message(reason, candidates, latency=None, throughput_kbps=None):
    latency_text = f"{latency:.3f}s" if latency is not None else "unknown"
    throughput_text = f"{throughput_kbps:.0f} kbps" if throughput_kbps is not None else "unknown"
    fallback = candidates[0] if candidates else None
    return (
        "proxy-xray native balancer recovered\n"
        f"reason: {reason}\n"
        "selected: handled by Xray balancer\n"
        f"fallback:\n{candidate_description(fallback)}\n"
        f"candidates: {len(candidates)}\n"
        f"health latency: {latency_text}\n"
        f"throughput: {throughput_text}"
    )
