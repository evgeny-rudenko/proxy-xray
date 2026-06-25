import socket
import subprocess
import time


def health_item(status, label, detail="", latency=None):
    return {
        "status": status,
        "label": label,
        "detail": detail,
        "latency": round(latency, 3) if latency is not None else None,
        "time": time.time(),
    }


def curl_probe(label, url, timeout, proxy=None, expected_codes=None):
    expected_codes = expected_codes or {"204"}
    command = [
        "curl",
        "-o",
        "/dev/null",
        "-sS",
        "--max-time",
        str(timeout),
        "-w",
        "%{http_code} %{time_total}",
    ]
    if proxy:
        command.extend(["-x", proxy])
    command.append(url)
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        return health_item("fail", label, f"timeout after {timeout}s", float(timeout))

    parts = result.stdout.strip().split()
    code = parts[0] if parts else "000"
    try:
        latency = float(parts[1]) if len(parts) > 1 else None
    except ValueError:
        latency = None

    if result.returncode == 0 and code in expected_codes:
        return health_item("ok", label, f"HTTP {code}", latency)
    detail = f"HTTP {code}"
    if result.stderr.strip():
        detail = result.stderr.strip().splitlines()[-1]
    return health_item("fail", label, detail, latency)


def tcp_probe(label, host, port, timeout):
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return health_item("ok", label, f"{host}:{port}", time.monotonic() - started)
    except OSError as exc:
        return health_item("fail", label, str(exc), time.monotonic() - started)


def dns_probe(label, domain, server, timeout):
    command = ["nslookup", domain, server]
    started = time.monotonic()
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return health_item("fail", label, f"timeout resolving {domain}", float(timeout))
    latency = time.monotonic() - started
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode == 0 and "Address" in output:
        return health_item("ok", label, domain, latency)
    return health_item("fail", label, f"failed resolving {domain}", latency)


def status_from_existing(label, value, ok_status="ok"):
    if not value:
        return health_item("unknown", label, "not checked yet")
    status = value.get("status")
    if status == ok_status:
        detail = "health check OK"
        if value.get("latency") is not None:
            detail = "generate_204"
        if value.get("kbps") is not None:
            detail = f"{value.get('kbps')} kbps"
        return health_item("ok", label, detail, value.get("latency"))
    return health_item("fail", label, status or "failed")


def subscription_health(status):
    if not status:
        return health_item("unknown", "Subscription", "not checked yet")
    return health_item(status.get("status", "unknown"), "Subscription", status.get("detail", ""))


def telegram_health(args, timeout):
    if not args.telegram_bot_token or not args.telegram_chat_id:
        return health_item("unknown", "Telegram", "not configured")
    return curl_probe(
        "Telegram API",
        "https://api.telegram.org",
        timeout,
        proxy="socks5h://127.0.0.1:1080",
        expected_codes={"200", "302", "404"},
    )


def build_health_checks(args, xray_running, last_health, last_quality, last_throughput, subscription_status):
    timeout = args.diagnostics_timeout
    checks = {
        "xray_process": health_item("ok" if xray_running else "fail", "Xray process", "running" if xray_running else "stopped"),
        "socks_proxy": status_from_existing("SOCKS proxy", last_health),
        "quality_download": status_from_existing("Quality download", last_quality),
        "throughput": status_from_existing("Throughput", last_throughput),
        "subscription": subscription_health(subscription_status),
        "direct_internet": curl_probe("Direct internet", args.health_url, timeout),
        "http_proxy": curl_probe("HTTP proxy", args.health_url, timeout, proxy="http://127.0.0.1:8123"),
        "lan_vless": tcp_probe("LAN VLESS inbound", "127.0.0.1", args.inbound_vless_port, timeout)
        if args.inbound_vless
        else health_item("unknown", "LAN VLESS inbound", "disabled"),
        "dns_ru": dns_probe("RU DNS", "yandex.ru", "127.0.0.1", timeout),
        "dns_global": dns_probe("Global DNS", "google.com", "127.0.0.1", timeout),
        "telegram": telegram_health(args, timeout),
    }
    return checks
