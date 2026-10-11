import base64
import re
import subprocess
import tempfile
import urllib.request
import time

from .state import apply_persisted_state, load_cached_subscription_candidates
from .status import log, set_status
from .util import csv
from .vless import filter_and_rank, merge_candidate_state, parse_subscription, parse_vless_lines, parse_vless_uri


USER_AGENT = "proxy-xray-subscription-supervisor/1.0"


def normalized_headers(headers):
    return {
        str(key).strip().lower(): str(value).strip()
        for key, value in (headers or {}).items()
        if key and value is not None
    }


def parse_curl_headers(raw):
    """Return headers from the final HTTP response emitted by curl -D."""
    blocks = re.split(br"\r?\n\r?\n", raw or b"")
    for block in reversed(blocks):
        lines = block.splitlines()
        if not lines or not lines[0].startswith(b"HTTP/"):
            continue
        headers = {}
        for line in lines[1:]:
            if b":" not in line:
                continue
            name, value = line.split(b":", 1)
            headers[name.decode("utf-8", "ignore").strip().lower()] = value.decode(
                "utf-8", "ignore"
            ).strip()
        return headers
    return {}


def parse_subscription_usage(headers, fetched_at=None):
    """Parse the common Subscription-Userinfo quota response header."""
    raw = normalized_headers(headers).get("subscription-userinfo")
    if not raw:
        return None
    fields = {}
    for part in raw.split(";"):
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue
        try:
            fields[key.strip().lower()] = int(value.strip())
        except ValueError:
            continue
    total = fields.get("total")
    if total is None or total <= 0:
        return None
    upload = max(0, fields.get("upload", 0))
    download = max(0, fields.get("download", 0))
    used = upload + download
    expires_at = fields.get("expire")
    return {
        "upload_bytes": upload,
        "download_bytes": download,
        "used_bytes": used,
        "total_bytes": total,
        "remaining_bytes": max(0, total - used),
        "usage_percent": round(used * 100 / total, 1),
        "expires_at": expires_at if expires_at and expires_at > 0 else None,
        "fetched_at": fetched_at if fetched_at is not None else time.time(),
    }


def fetch_subscription_direct(url, timeout):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read(), normalized_headers(response.headers)


def fetch_subscription_proxy(url, timeout, proxy_url):
    if not proxy_url:
        raise RuntimeError("subscription proxy URL is empty")
    with tempfile.NamedTemporaryFile() as headers_file:
        result = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                str(timeout),
                "-A",
                USER_AGENT,
                "-H",
                "Accept: text/plain,*/*",
                "-x",
                proxy_url,
                "-D",
                headers_file.name,
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        headers_file.seek(0)
        headers = parse_curl_headers(headers_file.read())
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "ignore").strip()
        raise RuntimeError(detail or f"curl exited with code {result.returncode}")
    return result.stdout, headers


def fetch_subscription(args, allow_proxy=False):
    mode = getattr(args, "sub_fetch_mode", "auto")
    proxy_url = getattr(args, "sub_fetch_proxy", "socks5h://127.0.0.1:1080")
    attempts = []
    errors = []
    if mode in ("direct", "auto"):
        attempts.append(("direct", lambda: fetch_subscription_direct(args.sub_url, args.fetch_timeout)))
    if mode in ("proxy", "auto"):
        if allow_proxy:
            attempts.append(
                ("proxy", lambda: fetch_subscription_proxy(args.sub_url, args.fetch_timeout, proxy_url))
            )
        elif mode == "proxy":
            errors.append("proxy fetch requested before local proxy is available")

    for method, fetcher in attempts:
        try:
            raw, headers = fetcher()
            return raw, method, headers
        except Exception as exc:
            errors.append(f"{method}: {type(exc).__name__}")
            log(f"subscription {method} fetch failed ({type(exc).__name__})")
    raise RuntimeError("; ".join(errors) or "no subscription fetch method is enabled")


def decode_subscription(raw):
    text = raw.decode("utf-8", "ignore").strip()
    if "vless://" in text:
        return text

    compact = re.sub(r"\s+", "", text)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            padded = compact + ("=" * (-len(compact) % 4))
            decoded = decoder(padded.encode("ascii")).decode("utf-8", "ignore")
            if "vless://" in decoded:
                return decoded
        except Exception:
            pass
    return text


def load_extra_candidates(args):
    candidates = []
    start_index = 100000
    if args.extra_file:
        try:
            with open(args.extra_file, "r", encoding="utf-8") as handle:
                candidates.extend(parse_vless_lines(handle.read(), "extra", 0, start_index))
                start_index += len(candidates)
        except FileNotFoundError:
            log(f"extra VLESS file not found: {args.extra_file}")
    for uri in args.extra_vless:
        item = parse_vless_uri(uri, start_index)
        if item:
            item["source"] = "extra"
            item["source_score"] = 0
            candidates.append(item)
            start_index += 1
    return candidates


def load_candidates(args, old_candidates=None, state=None, allow_proxy=False):
    extra = load_extra_candidates(args)
    parsed = []
    fetch_mode = getattr(args, "sub_fetch_mode", "auto")
    fetch_proxy = getattr(args, "sub_fetch_proxy", "socks5h://127.0.0.1:1080")
    log("refreshing subscription")
    cached_usage = (state or {}).get("subscription_usage")
    if cached_usage:
        set_status(subscription_usage=cached_usage)
    set_status(
        subscription_fetch={
            "mode": fetch_mode,
            "proxy": fetch_proxy if fetch_mode in ("proxy", "auto") else None,
            "proxy_allowed": allow_proxy,
            "last_method": None,
        },
        last_subscription_status={"status": "unknown", "detail": "refreshing", "time": time.time()},
    )
    try:
        raw, method, headers = fetch_subscription(args, allow_proxy=allow_proxy)
        text = decode_subscription(raw)
        parsed = parse_subscription(text)
        subscription_usage = parse_subscription_usage(headers)
        set_status(
            last_subscription_success_at=time.time(),
            subscription_usage=subscription_usage,
            subscription_fetch={
                "mode": fetch_mode,
                "proxy": fetch_proxy if fetch_mode in ("proxy", "auto") else None,
                "proxy_allowed": allow_proxy,
                "last_method": method,
            },
            last_subscription_status={
                "status": "ok",
                "detail": f"loaded {len(parsed)} subscription links via {method}",
                "time": time.time(),
            },
        )
    except Exception:
        cached = load_cached_subscription_candidates(state or {})
        if cached:
            parsed = cached
            log(f"subscription refresh failed; using {len(cached)} cached subscription links")
            set_status(
                last_subscription_status={
                    "status": "warn",
                    "detail": f"using {len(cached)} cached subscription links",
                    "time": time.time(),
                }
            )
        elif not extra:
            set_status(
                last_subscription_status={
                    "status": "fail",
                    "detail": "subscription refresh failed and no fallback candidates are available",
                    "time": time.time(),
                }
            )
            raise
        else:
            log("subscription refresh failed; using extra VLESS links only")
            set_status(
                last_subscription_status={
                    "status": "warn",
                    "detail": "using extra VLESS links only",
                    "time": time.time(),
                }
            )
    prefer = [item.lower() for item in csv(args.prefer)]
    excludes = [item.lower() for item in csv(args.exclude)]
    candidates = extra + filter_and_rank(parsed, prefer, excludes)
    if state:
        candidates = apply_persisted_state(candidates, state)
        last_selected_uri = state.get("last_selected_uri")
        for candidate in candidates:
            candidate["last_selected"] = candidate["uri"] == last_selected_uri
    if old_candidates:
        candidates = merge_candidate_state(old_candidates, candidates)
    log(
        f"loaded {len(parsed)} subscription links, {len(extra)} extra links, "
        f"{len(candidates)} candidates after filtering"
    )
    return candidates
