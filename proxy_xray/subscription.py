import base64
import re
import subprocess
import urllib.request
import time

from .state import apply_persisted_state, load_cached_subscription_candidates
from .status import log, set_status
from .util import csv
from .vless import filter_and_rank, merge_candidate_state, parse_subscription, parse_vless_lines, parse_vless_uri


USER_AGENT = "proxy-xray-subscription-supervisor/1.0"


def fetch_subscription_direct(url, timeout):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def fetch_subscription_proxy(url, timeout, proxy_url):
    if not proxy_url:
        raise RuntimeError("subscription proxy URL is empty")
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
            url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "ignore").strip()
        raise RuntimeError(detail or f"curl exited with code {result.returncode}")
    return result.stdout


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
            return fetcher(), method
        except Exception as exc:
            errors.append(f"{method}: {exc}")
            log(f"subscription {method} fetch failed: {exc}")
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
        raw, method = fetch_subscription(args, allow_proxy=allow_proxy)
        text = decode_subscription(raw)
        parsed = parse_subscription(text)
        set_status(
            last_subscription_success_at=time.time(),
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
    candidates = filter_and_rank(extra + parsed, prefer, excludes)
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
