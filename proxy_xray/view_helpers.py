import html
import json
import os
import time
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .diagnostics import build_diagnostics
from .qr import qr_svg
from .status import status_snapshot

def status_timezone():
    timezone_name = os.environ.get("TZ") or "UTC"
    try:
        return timezone_name, ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return "UTC", ZoneInfo("UTC")


def timezone_label():
    timezone_name, timezone = status_timezone()
    now = datetime.now(timezone)
    offset = now.strftime("%z")
    if offset:
        offset = f"UTC{offset[:3]}:{offset[3:]}"
    else:
        offset = "UTC"
    return f"{timezone_name} ({offset})"


def format_time(value):
    if not value:
        return "-"
    _, timezone = status_timezone()
    return datetime.fromtimestamp(value, timezone).strftime("%Y-%m-%d %H:%M:%S")


def format_metric(value, suffix=""):
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}{suffix}"
    return f"{value}{suffix}"


def status_class(status):
    if status == "ok":
        return "ok"
    if status == "warn" or status == "degraded":
        return "warn"
    if status == "fail" or status == "failed":
        return "fail"
    return "unknown"


def health_counts(health_checks):
    counts = {"ok": 0, "warn": 0, "fail": 0, "unknown": 0}
    for item in health_checks.values():
        counts[status_class(item.get("status"))] += 1
    return counts


def health_card(key, item):
    status = item.get("status") or "unknown"
    label = item.get("label") or key
    detail = item.get("detail") or "-"
    latency = item.get("latency")
    latency_text = f"{latency:.3f}s" if isinstance(latency, (int, float)) else ""
    return (
        f'<div class="health-card {status_class(status)}">'
        f'<div class="health-top"><span>{html.escape(label)}</span>'
        f'<strong>{html.escape(str(status).upper())}</strong></div>'
        f'<div class="health-detail">{html.escape(str(detail))}</div>'
        f'<div class="health-latency">{html.escape(latency_text)}</div>'
        "</div>"
    )


def render_health_grid(health_checks):
    order = [
        "xray_process",
        "socks_proxy",
        "http_proxy",
        "lan_vless",
        "throughput",
        "direct_internet",
        "subscription",
        "dns_ru",
        "dns_global",
        "telegram",
    ]
    cards = [health_card(key, health_checks[key]) for key in order if key in health_checks]
    for key, item in health_checks.items():
        if key not in order:
            cards.append(health_card(key, item))
    if not cards:
        cards.append('<div class="health-card unknown"><div class="health-top"><span>Health</span><strong>UNKNOWN</strong></div><div class="health-detail">not checked yet</div></div>')
    return "".join(cards)


def candidate_rows(candidates):
    rows = []
    for candidate in candidates:
        endpoint = f"{candidate.get('host') or ''}:{candidate.get('port') or ''}"
        transport = f"{candidate.get('network') or ''}/{candidate.get('security') or ''}"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(candidate.get('tag') or ''))}</td>"
            f"<td>{html.escape(str(candidate.get('source') or ''))}</td>"
            f"<td>{html.escape(str(candidate.get('name') or ''))}</td>"
            f"<td>{html.escape(endpoint)}</td>"
            f"<td>{html.escape(transport)}</td>"
            f"<td>{html.escape(format_metric(candidate.get('fallback_score')))}<br>{html.escape(', '.join(candidate.get('fallback_score_reasons') or []))}</td>"
            f"<td>{html.escape(format_metric(candidate.get('last_latency'), 's'))}</td>"
            f"<td>{html.escape(format_metric(candidate.get('last_throughput_kbps'), ' kbps'))}</td>"
            f"<td>{html.escape(format_time(candidate.get('last_ok_at')))}</td>"
            "</tr>"
        )
    return "".join(rows)


def candidate_table(candidates, empty_text):
    rows = candidate_rows(candidates)
    if not rows:
        rows = f'<tr><td colspan="9">{html.escape(empty_text)}</td></tr>'
    return (
        '<div class="table-wrap"><table>'
        "<tr><th>Tag</th><th>Source</th><th>Name</th><th>Endpoint</th>"
        "<th>Transport</th><th>Score</th><th>Latency</th><th>Speed</th><th>Last OK</th></tr>"
        f"{rows}"
        "</table></div>"
    )


def bytes_text(value):
    if not value:
        return "-"
    units = ["B", "KB", "MB", "GB"]
    amount = float(value)
    unit = 0
    while amount >= 1024 and unit < len(units) - 1:
        amount /= 1024
        unit += 1
    return f"{amount:.1f} {units[unit]}"


def subscription_quota_badge(usage):
    if not isinstance(usage, dict) or not usage.get("total_bytes"):
        return ""
    used = bytes_text(usage.get("used_bytes"))
    total = bytes_text(usage.get("total_bytes"))
    percent = min(100, max(0, float(usage.get("usage_percent") or 0)))
    expires_at = usage.get("expires_at")
    expiry = format_time(expires_at) if expires_at else "no expiry date"
    return (
        '<div class="v5-quota" title="Subscription traffic limit">'
        f'<div><span>Traffic</span><strong>{html.escape(used)} / {html.escape(total)}</strong></div>'
        f'<small>{percent:.1f}% used · until {html.escape(expiry)}</small>'
        f'<div class="v5-quota-track"><i style="width:{percent:.1f}%"></i></div>'
        "</div>"
    )


def assets_table(assets):
    items = assets.get("items") or {}
    rows = []
    for name, item in items.items():
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(name))}</td>"
            f"<td>{html.escape(str(item.get('status') or '-'))}</td>"
            f"<td>{html.escape(bytes_text(item.get('size')))}</td>"
            f"<td>{html.escape(format_time(item.get('mtime')))}</td>"
            f"<td>{html.escape(format_time(item.get('last_success_at')))}</td>"
            f"<td>{html.escape(str(item.get('last_error') or '-'))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6">No asset data</td></tr>')
    return (
        '<div class="table-wrap"><table>'
        "<tr><th>Asset</th><th>Status</th><th>Size</th><th>Local File Time</th>"
        "<th>Last Downloaded</th><th>Last Error</th></tr>"
        f"{''.join(rows)}"
        "</table></div>"
    )


def endpoint_text(candidate):
    if not candidate:
        return "-"
    return f"{candidate.get('host') or ''}:{candidate.get('port') or ''}"


def throughput_text(value):
    if not value:
        return "-"
    try:
        kbps = float(value)
    except (TypeError, ValueError):
        return str(value)
    if kbps >= 1000:
        return f"{kbps / 1000:.1f} Mbps"
    return f"{kbps:.0f} kbps"


def short_time(value):
    if not value:
        return "-"
    _, timezone = status_timezone()
    return datetime.fromtimestamp(value, timezone).strftime("%H:%M:%S")


def seconds_left(value):
    if not value:
        return "-"
    remaining = int(float(value) - time.time())
    if remaining <= 0:
        return "-"
    return f"{remaining}s"


def health_title(counts):
    fail = counts.get("fail", 0)
    warn = counts.get("warn", 0)
    ok = counts.get("ok", 0)
    if fail:
        return "Gateway has failures"
    if warn:
        return "Gateway is degraded"
    if ok:
        return "Gateway is healthy"
    return "Gateway status unknown"


def modern_health_card(key, item):
    status = item.get("status") or "unknown"
    label = item.get("label") or key
    detail = item.get("detail") or "-"
    latency = item.get("latency")
    latency_text = f"{latency:.3f}s" if isinstance(latency, (int, float)) else ""
    status_name = str(status).upper()
    return (
        f'<div class="health-item {status_class(status)}">'
        '<div class="health-top">'
        f'<div class="health-name">{html.escape(str(label))}</div>'
        f'<div class="health-state {status_class(status)}">{html.escape(status_name)}</div>'
        "</div>"
        f'<div class="health-detail">{html.escape(str(detail))}</div>'
        f'<div class="health-meta">{html.escape(latency_text)}</div>'
        "</div>"
    )


def render_modern_health_grid(health_checks):
    order = [
        "xray_process",
        "socks_proxy",
        "http_proxy",
        "lan_vless",
        "quality_download",
        "dns_ru",
        "dns_global",
        "telegram",
        "throughput",
    ]
    cards = [modern_health_card(key, health_checks[key]) for key in order if key in health_checks]
    for key, item in health_checks.items():
        if key not in order:
            cards.append(modern_health_card(key, item))
    if not cards:
        cards.append(
            '<div class="health-item unknown"><div class="health-top">'
            '<div class="health-name">Health</div><div class="health-state unknown">UNKNOWN</div>'
            '</div><div class="health-detail">not checked yet</div></div>'
        )
    return "".join(cards)


def score_bar(candidate):
    score = candidate.get("fallback_score")
    if score is None:
        score_text = "-"
        width = 0
    else:
        try:
            score_num = float(score)
        except (TypeError, ValueError):
            score_num = 0
        score_text = format_metric(score)
        width = max(0, min(100, int(score_num)))
    return (
        '<div class="score-stack">'
        '<div class="score">'
        f"<strong>{html.escape(score_text)}</strong>"
        f'<div class="score-bar"><span style="width: {width}%"></span></div>'
        "</div>"
        f'<div class="score-reasons">{html.escape(score_reasons(candidate, limit=6))}</div>'
        "</div>"
    )


def score_value(candidate):
    return format_metric(candidate.get("fallback_score")) if candidate.get("fallback_score") is not None else "-"


def score_reasons(candidate, limit=2):
    reasons = candidate.get("fallback_score_reasons") or []
    if not reasons:
        return "no score reasons"
    return ", ".join(str(reason) for reason in reasons[:limit])


def modern_server_rows(candidates):
    rows = []
    for candidate in candidates:
        endpoint = f"{candidate.get('host') or ''}:{candidate.get('port') or ''}"
        transport = f"{candidate.get('network') or ''}/{candidate.get('security') or ''}"
        rows.append(
            "<tr>"
            f"<td>{score_bar(candidate)}</td>"
            f"<td>{html.escape(str(candidate.get('tag') or ''))}<br>"
            f'<span class="metric-note">{html.escape(str(candidate.get("name") or ""))}</span></td>'
            f"<td>{html.escape(endpoint)}</td>"
            f"<td>{html.escape(transport)}</td>"
            f"<td>{html.escape(format_metric(candidate.get('last_latency'), 's'))}</td>"
            f"<td>{html.escape(short_time(candidate.get('last_ok_at')))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="6">No servers yet</td></tr>')
    return "".join(rows)


def compact_server_cards(candidates, empty_text="No servers in this pool"):
    cards = []
    for candidate in candidates:
        endpoint = endpoint_text(candidate)
        transport = f"{candidate.get('network') or '-'}/{candidate.get('security') or '-'}"
        cards.append(
            '<div class="v5-server-card">'
            '<div>'
            f'<div class="v5-server-tag">{html.escape(str(candidate.get("tag") or "-"))}</div>'
            f'<div class="v5-muted">{html.escape(str(candidate.get("name") or ""))}</div>'
            "</div>"
            '<div class="v5-server-meta">'
            f"<strong>{html.escape(score_value(candidate))}</strong>"
            f"<span>{html.escape(endpoint)} · {html.escape(transport)}</span>"
            f"<span>{html.escape(format_metric(candidate.get('last_latency'), 's'))} · {html.escape(short_time(candidate.get('last_ok_at')))}</span>"
            "</div>"
            "</div>"
        )
    if not cards:
        cards.append(f'<div class="v5-empty">{html.escape(empty_text)}</div>')
    return "".join(cards)


def compact_health_rows(health_checks):
    order = [
        "xray_process",
        "socks_proxy",
        "http_proxy",
        "lan_vless",
        "quality_download",
        "throughput",
        "dns_ru",
        "dns_global",
        "telegram",
        "subscription",
        "direct_internet",
    ]
    rows = []
    ordered = [(key, health_checks[key]) for key in order if key in health_checks]
    ordered.extend((key, item) for key, item in health_checks.items() if key not in order)
    for key, item in ordered:
        status = item.get("status") or "unknown"
        label = item.get("label") or key
        detail = item.get("detail") or "-"
        latency = item.get("latency")
        latency_text = f"{latency:.3f}s" if isinstance(latency, (int, float)) else "-"
        css_class = status_class(status)
        rows.append(
            f'<div class="v5-health-row {css_class}">'
            '<div class="v5-health-main">'
            f'<span class="v5-dot"></span><strong>{html.escape(str(label))}</strong>'
            f'<small>{html.escape(str(detail))}</small>'
            "</div>"
            f'<div class="v5-health-side"><span>{html.escape(str(status).upper())}</span><small>{html.escape(latency_text)}</small></div>'
            "</div>"
        )
    if not rows:
        rows.append('<div class="v5-empty">No health checks yet</div>')
    return "".join(rows)


def event_timeline_rows(logs):
    rows = []
    for item in newest_logs(logs, limit=12):
        line = str(item.get("line") or "")
        lower = line.lower()
        if "fail" in lower or "timeout" in lower or "degrad" in lower:
            tone = "fail"
        elif "warn" in lower or "fallback" in lower or "switch" in lower:
            tone = "warn"
        elif "ok" in lower:
            tone = "ok"
        else:
            tone = "neutral"
        rows.append(
            f'<div class="v5-event {tone}">'
            f'<time>{html.escape(short_time(item.get("time")))}</time>'
            f'<span>{html.escape(line)}</span>'
            "</div>"
        )
    if not rows:
        rows.append('<div class="v5-empty">No events yet</div>')
    return "".join(rows)


def newest_logs(logs, limit=14):
    return list(reversed(list(logs or [])[-limit:]))


def recent_log_text(logs, limit=14):
    return "\n".join(f"{format_time(item.get('time'))} {item.get('line', '')}" for item in newest_logs(logs, limit=limit))
