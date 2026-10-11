import ipaddress
import os
import time
from urllib.parse import urlparse

from .util import load_json_file


def hostname_from_url(value):
    try:
        return (urlparse(value).hostname or "").strip().lower()
    except (TypeError, ValueError):
        return ""


def is_hostname(value):
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return False
    except ValueError:
        return True


def bootstrap_domains(args, candidates):
    domains = set()
    subscription_host = hostname_from_url(getattr(args, "sub_url", ""))
    if is_hostname(subscription_host):
        domains.add(subscription_host)
    for candidate in candidates or []:
        host = str(candidate.get("host") or "").strip().lower().rstrip(".")
        if is_hostname(host):
            domains.add(host)
    return sorted(domains)


def write_bootstrap_domains(args, candidates):
    path = getattr(args, "dns_bootstrap_file", None)
    if not path:
        return []
    domains = bootstrap_domains(args, candidates)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        for domain in domains:
            handle.write(f"{domain}\n")
    os.replace(tmp, path)
    return domains


def set_dns_proxy_ready(args, ready):
    path = getattr(args, "dns_proxy_ready_file", None)
    if not path:
        return
    if not ready:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="ascii") as handle:
        handle.write(f"{time.time()}\n")
    os.replace(tmp, path)


def dns_transport_snapshot(args):
    route = getattr(args, "dns_global_route", "direct")
    status_file = getattr(args, "dns_status_file", None)
    try:
        runtime = load_json_file(status_file, {}) if status_file else {}
    except (OSError, TypeError, ValueError):
        runtime = {"status": "unknown", "error": "DNS status file is unreadable"}
    if not isinstance(runtime, dict):
        runtime = {"status": "unknown", "error": "invalid DNS status"}
    try:
        age = max(0, time.time() - float(runtime["time"])) if runtime.get("time") else None
    except (TypeError, ValueError):
        age = None
    return {
        "enabled": route != "direct",
        "global_route": route,
        "proxy": "socks://127.0.0.1:10953" if route != "direct" else None,
        "bootstrap_file": getattr(args, "dns_bootstrap_file", None),
        "proxy_ready": bool(
            getattr(args, "dns_proxy_ready_file", None)
            and os.path.exists(getattr(args, "dns_proxy_ready_file"))
        ),
        "direct_fallback": bool(getattr(args, "dns_global_direct_fallback", False)),
        "last_query_at": runtime.get("time"),
        "last_status": runtime.get("status"),
        "last_query_class": runtime.get("query_class"),
        "last_route": runtime.get("route"),
        "last_upstream": runtime.get("upstream"),
        "last_bootstrap": runtime.get("bootstrap"),
        "last_error": runtime.get("error"),
        "status_age": age,
    }
