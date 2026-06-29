import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .diagnostics import build_diagnostics
from .qr import qr_svg
from .status import LOG_BUFFER, log, status_snapshot


STATUS_ARGS = None


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
    for item in logs[-12:]:
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


def dashboard_v5_fragments(snapshot=None):
    snapshot = snapshot or status_snapshot()
    active_backend = snapshot.get("active_backend") or {}
    hot_standby = snapshot.get("hot_standby") or {}
    active_path = snapshot.get("active_path") or {}
    standby_observatory = snapshot.get("standby_observatory") or {}
    active_selected = active_path.get("selected") or {}
    standby_selected = standby_observatory.get("selected") or {}
    active_backend_candidate = active_backend.get("candidate") or {}
    hot_standby_candidate = hot_standby.get("candidate") or {}
    current = active_selected or active_backend_candidate or ((snapshot.get("active_pool") or [{}])[0])
    standby_display = standby_selected or hot_standby_candidate or ((snapshot.get("standby_pool") or [{}])[0])
    health_checks = snapshot.get("health_checks") or {}
    counts = health_counts(health_checks)
    failover = snapshot.get("failover_state") or {}
    sources = snapshot.get("sources") or {}
    assets = snapshot.get("assets") or {}
    subscription_fetch = snapshot.get("subscription_fetch") or {}
    last_throughput = snapshot.get("last_throughput") or {}
    tested_live = snapshot.get("tested_live_candidates") or []
    active_pool = snapshot.get("active_pool") or []
    standby_pool = snapshot.get("standby_pool") or []
    current_tag = active_path.get("selected_tag") or current.get("tag") or "-"
    current_transport = f"{current.get('network') or '-'} / {current.get('security') or '-'}"
    current_endpoint = endpoint_text(current)
    standby_tag = standby_display.get("tag") or "-"
    standby_endpoint = endpoint_text(standby_display) if standby_display else "-"
    xray_running = active_backend.get("running", snapshot.get("xray_running"))
    xray_class = "ok" if xray_running else "fail"
    api_class = "ok" if active_path.get("status") == "ok" else "warn"
    hot_class = "ok" if hot_standby.get("healthy") else "warn" if hot_standby.get("running") else "fail"
    if counts.get("fail", 0):
        gateway_class = "fail"
        gateway_title = "Gateway has failures"
        gateway_note = f'{counts.get("fail", 0)} failing checks need attention'
    elif counts.get("warn", 0):
        gateway_class = "warn"
        gateway_title = "Gateway is degraded"
        gateway_note = f'{counts.get("warn", 0)} warning checks, traffic still has a path'
    elif counts.get("ok", 0):
        gateway_class = "ok"
        gateway_title = "Gateway is healthy"
        gateway_note = f'{counts.get("ok", 0)} checks are green'
    else:
        gateway_class = "unknown"
        gateway_title = "Gateway status unknown"
        gateway_note = "health checks have not reported yet"
    pool_note = (
        f'active {len(active_pool)} · hot {len(standby_pool)} · '
        f'tested {len(tested_live)} of {snapshot.get("candidates_count", 0)}'
    )
    failover_note = failover.get("reason") or f"cooldown {failover.get('cooldown_remaining', 0)}s"
    return {
        "v5-subline": f"{html.escape(timezone_label())} · refreshed {html.escape(format_time(snapshot.get('last_health_checks_at')))} · live update 15s",
        "v5-overview": f"""
        <div class="v5-state {gateway_class}">
          <span class="v5-state-dot"></span>
          <div><h2>{html.escape(gateway_title)}</h2><p>{html.escape(gateway_note)}</p></div>
        </div>
        <div class="v5-kpi-row">
          <div class="v5-kpi"><span>Candidates</span><strong>{snapshot.get('candidates_count', 0)}</strong><small>extra {sources.get('extra', 0)} / sub {sources.get('subscription', 0)}</small></div>
          <div class="v5-kpi"><span>Tested live</span><strong>{len(tested_live)}</strong><small>fallback-ready list</small></div>
          <div class="v5-kpi"><span>Throughput</span><strong>{html.escape(throughput_text(last_throughput.get('kbps')))}</strong><small>active path</small></div>
          <div class="v5-kpi"><span>Next test</span><strong>{html.escape(short_time(snapshot.get('next_candidate_check_at')))}</strong><small>weighted random</small></div>
        </div>""",
        "v5-current": f"""
        <div class="v5-panel-title"><span>Current connection</span><a href="/json">JSON</a></div>
        <div class="v5-current-name">{html.escape(str(current_tag))}</div>
        <div class="v5-current-endpoint">{html.escape(current_endpoint)} · {html.escape(current_transport)}</div>
        <div class="v5-chip-row">
          <span class="v5-chip {xray_class}">xray {'running' if xray_running else 'stopped'}</span>
          <span class="v5-chip {api_class}">api {html.escape(str(active_path.get('status') or '-'))}</span>
          <span class="v5-chip ok">balancer {html.escape(str(active_path.get('strategy') or '-'))}</span>
          <span class="v5-chip {hot_class}">hot {html.escape(str(standby_tag))}</span>
        </div>
        <div class="v5-current-grid">
          <div><span>Score</span><strong>{html.escape(score_value(current))}</strong><small>{html.escape(score_reasons(current, limit=4))}</small></div>
          <div><span>Latency</span><strong>{html.escape(format_metric(current.get('last_latency'), 's'))}</strong><small>last OK</small></div>
          <div><span>Hot standby</span><strong>{html.escape(str(standby_tag))}</strong><small>{html.escape(standby_endpoint)}</small></div>
          <div><span>Switch guard</span><strong>{int(failover.get('cooldown_remaining') or 0)}s</strong><small>{html.escape(str(failover_note))}</small></div>
        </div>""",
        "v5-health": f"""
        <div class="v5-panel-title"><span>Health indicators</span><small>OK {counts.get('ok', 0)} · WARN {counts.get('warn', 0)} · FAIL {counts.get('fail', 0)}</small></div>
        <div class="v5-health-list">{compact_health_rows(health_checks)}</div>""",
        "v5-pools": f"""
        <div class="v5-panel-title"><span>Active and hot pools</span><small>{html.escape(pool_note)}</small></div>
        <div class="v5-pool-grid">
          <div><h3>Active pool</h3>{compact_server_cards(active_pool[:4], 'Active pool is empty')}</div>
          <div><h3>Hot pool</h3>{compact_server_cards(standby_pool[:4], 'Hot pool is empty')}</div>
        </div>""",
        "v5-servers": f"""
        <div class="v5-panel-title"><span>Live servers by score</span><a href="/servers/live">open full list</a></div>
        <div class="v5-server-list">{compact_server_cards(tested_live[:7], 'No tested live servers yet')}</div>""",
        "v5-routing": f"""
        <div class="v5-panel-title"><span>Routing and assets</span><a href="/diagnostics">Diagnostics</a></div>
        <div class="v5-routing-grid">
          <div><span>Geo assets</span><strong>{html.escape(str((assets.get('last_status') or {}).get('status') or '-'))}</strong><small>last {html.escape(short_time(assets.get('last_success_at')))}</small></div>
          <div><span>Subscription</span><strong>{html.escape(short_time(snapshot.get('last_subscription_success_at')))}</strong><small>{html.escape(str(subscription_fetch.get('last_method') or '-'))} / {html.escape(str(subscription_fetch.get('mode') or '-'))}</small></div>
          <div><span>Direct RU</span><strong>enabled</strong><small>geoip:ru · geosite:category-ru</small></div>
          <div><span>Failover</span><strong>{html.escape(str(failover.get('state') or 'idle'))}</strong><small>{html.escape(str(failover_note))}</small></div>
        </div>""",
        "v5-events": f"""
        <div class="v5-panel-title"><span>Event timeline</span><a href="/logs">plain logs</a></div>
        <div class="v5-events">{event_timeline_rows(snapshot.get('logs') or [])}</div>""",
    }


def probe_status_class(item):
    return status_class((item or {}).get("status"))


def diagnostic_probe_rows(probes):
    rows = []
    for probe in probes:
        for path_name in ("direct", "socks", "http"):
            item = probe.get(path_name) or {}
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(probe.get('url') or '-'))}</td>"
                f"<td>{html.escape(path_name)}</td>"
                f'<td><span class="pill {probe_status_class(item)}">{html.escape(str(item.get("status") or "-"))}</span></td>'
                f"<td>{html.escape(str(item.get('detail') or '-'))}</td>"
                f"<td>{html.escape(format_metric(item.get('latency'), 's'))}</td>"
                "</tr>"
            )
    if not rows:
        rows.append('<tr><td colspan="5">No diagnostic probes configured.</td></tr>')
    return "".join(rows)


def dns_diagnostic_rows(dns):
    rows = []
    for name, item in (dns or {}).items():
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(name))}</td>"
            f'<td><span class="pill {probe_status_class(item)}">{html.escape(str(item.get("status") or "-"))}</span></td>'
            f"<td>{html.escape(str(item.get('detail') or '-'))}</td>"
            f"<td>{html.escape(format_metric(item.get('latency'), 's'))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="4">No DNS diagnostics.</td></tr>')
    return "".join(rows)


def host_from_header(host_header):
    host_header = (host_header or "").split(",", 1)[0].strip()
    if not host_header:
        return "127.0.0.1"
    if host_header.startswith("["):
        end = host_header.find("]")
        if end > 0:
            return host_header[1:end]
    return host_header.rsplit(":", 1)[0]


def client_connection_url(args, host):
    uuid = getattr(args, "inbound_vless_id", None)
    port = getattr(args, "inbound_vless_port", 10086)
    if not uuid:
        return None
    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"vless://{uuid}@{display_host}:{port}?security=none&type=tcp#{quote('home-proxy')}"


def render_client_html(args, host_header):
    host = host_from_header(host_header)
    connection_url = client_connection_url(args, host)
    warning = ""
    if host in ("127.0.0.1", "localhost", "::1"):
        warning = (
            "This page was opened through a loopback address. Open the status UI by the server LAN IP "
            "to generate a QR code for another device."
        )
    if connection_url:
        try:
            qr_markup = qr_svg(connection_url)
        except ValueError as exc:
            qr_markup = f'<div class="empty">{html.escape(str(exc))}</div>'
    else:
        qr_markup = '<div class="empty">LAN VLESS inbound UUID is not configured.</div>'
        warning = "Set INBOUND_VLESS_ID in .env and restart the container."

    connection_text = connection_url or "-"
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LAN VLESS client · proxy-xray</title>
  <style>
    :root {{ color-scheme: light; --bg: #eef2f5; --panel: #fff; --text: #17212b; --muted: #61707f; --line: #dbe2e8; --blue: #2563a7; --blue-soft: #e9f2ff; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: linear-gradient(180deg, #f7fafc 0, var(--bg) 300px), var(--bg); color: var(--text); font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(980px, calc(100% - 32px)); margin: 0 auto; padding: 24px 0 42px; }}
    .topbar {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 18px; }}
    h1 {{ margin: 0; font-size: 24px; line-height: 1.1; }}
    .muted {{ color: var(--muted); font-size: 13px; margin-top: 6px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .button {{ min-height: 34px; border: 1px solid #c9d3dc; background: #fff; color: #273545; border-radius: 8px; padding: 7px 12px; font-size: 13px; text-decoration: none; display: inline-grid; place-items: center; }}
    .button.primary {{ color: var(--blue); background: var(--blue-soft); border-color: #c6dcf6; }}
    .panel {{ background: rgba(255, 255, 255, 0.94); border: 1px solid var(--line); border-radius: 8px; box-shadow: 0 10px 28px rgba(30, 45, 62, 0.08); padding: 18px; }}
    .client-grid {{ display: grid; grid-template-columns: minmax(260px, 360px) minmax(0, 1fr); gap: 18px; align-items: start; }}
    .qr-box {{ background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 14px; }}
    .qr-box svg {{ width: 100%; height: auto; display: block; }}
    .label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 750; margin-bottom: 7px; }}
    .connection {{ width: 100%; min-height: 132px; resize: vertical; border: 1px solid var(--line); border-radius: 8px; padding: 11px; background: #f8fafb; color: #17212b; font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow-wrap: anywhere; }}
    .meta-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: 12px; }}
    .meta {{ border: 1px solid var(--line); border-radius: 8px; background: #f8fafb; padding: 10px; min-height: 66px; }}
    .value {{ font-size: 18px; font-weight: 750; overflow-wrap: anywhere; }}
    .warn {{ margin-top: 12px; border: 1px solid #f1d39b; background: #fff7e6; color: #714600; border-radius: 8px; padding: 10px 12px; font-size: 13px; }}
    .empty {{ min-height: 260px; display: grid; place-items: center; color: var(--muted); text-align: center; }}
    @media (max-width: 760px) {{ .topbar, .client-grid {{ display: block; }} .actions {{ justify-content: flex-start; margin-top: 12px; }} .qr-box {{ margin-bottom: 14px; }} .meta-grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <header class="topbar">
    <div>
      <h1>LAN VLESS client</h1>
      <div class="muted">Generated from this status page address · {html.escape(timezone_label())}</div>
    </div>
    <div class="actions"><a class="button" href="/">Status</a><a class="button" href="/json">JSON</a><button class="button primary" type="button" onclick="copyConnection()">Copy</button></div>
  </header>
  <section class="panel client-grid">
    <div class="qr-box">{qr_markup}</div>
    <div>
      <div class="label">Connection string</div>
      <textarea id="connection" class="connection" readonly>{html.escape(connection_text)}</textarea>
      <div class="meta-grid">
        <div class="meta"><div class="label">Server</div><div class="value">{html.escape(host)}</div></div>
        <div class="meta"><div class="label">Port</div><div class="value">{html.escape(str(getattr(args, 'inbound_vless_port', 10086)))}</div></div>
        <div class="meta"><div class="label">Security</div><div class="value">none</div></div>
        <div class="meta"><div class="label">Transport</div><div class="value">tcp</div></div>
      </div>
      {f'<div class="warn">{html.escape(warning)}</div>' if warning else ''}
    </div>
  </section>
</main>
<script>
function copyConnection() {{
  const field = document.getElementById('connection');
  field.focus();
  field.select();
  navigator.clipboard?.writeText(field.value).catch(() => document.execCommand('copy'));
}}
</script>
</body>
</html>"""
    return body.encode("utf-8")


def render_diagnostics_html(args):
    data = build_diagnostics(args)
    summary = data.get("summary") or {}
    active = data.get("active") or {}
    standby = data.get("standby") or {}
    failover = summary.get("failover_state") or {}
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>proxy-xray diagnostics</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f5f7f9; color: #17202a; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 22px; }}
    h1 {{ margin: 0; font-size: 24px; }}
    h2 {{ margin: 24px 0 10px; font-size: 18px; }}
    .topbar {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-end; }}
    .muted {{ color: #657386; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; margin-top: 14px; }}
    .box {{ background: #fff; border: 1px solid #d9e0e7; border-radius: 8px; padding: 12px; }}
    .label {{ color: #657386; font-size: 11px; text-transform: uppercase; font-weight: 750; }}
    .value {{ margin-top: 5px; font-size: 16px; overflow-wrap: anywhere; }}
    .table-wrap {{ overflow-x: auto; background: #fff; border: 1px solid #d9e0e7; border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 760px; }}
    th, td {{ text-align: left; padding: 9px 10px; border-bottom: 1px solid #edf1f4; font-size: 13px; vertical-align: top; }}
    th {{ background: #f0f4f7; color: #4f5f70; text-transform: uppercase; font-size: 11px; }}
    tr:last-child td {{ border-bottom: 0; }}
    .pill {{ display: inline-block; border: 1px solid #d4dbe2; border-radius: 999px; padding: 2px 8px; font-size: 12px; }}
    .pill.ok {{ color: #17633a; border-color: #b9dfca; background: #effaf3; }}
    .pill.warn {{ color: #7a4b00; border-color: #f1d39b; background: #fff7e6; }}
    .pill.fail {{ color: #8c1d18; border-color: #efb4ae; background: #fff0ee; }}
    a {{ color: #005ea8; }}
  </style>
</head>
<body>
<main>
  <div class="topbar">
    <div>
      <h1>proxy-xray diagnostics</h1>
      <div class="muted">{html.escape(timezone_label())} · generated {html.escape(format_time(data.get('generated_at')))}</div>
    </div>
    <div class="muted"><a href="/">status</a> · <a href="/diagnostics.json">json</a></div>
  </div>
  <section class="grid">
    <div class="box"><div class="label">Failover</div><div class="value">{html.escape(str(failover.get('state') or '-'))}<br>{html.escape(str(failover.get('reason') or failover.get('kind') or '-'))}</div></div>
    <div class="box"><div class="label">Active</div><div class="value">{html.escape(str(active.get('slot') or '-'))}<br>{html.escape(str((active.get('selected') or {}).get('tag') or active.get('selected_tag') or '-'))}</div></div>
    <div class="box"><div class="label">Standby</div><div class="value">{html.escape(str(standby.get('slot') or '-'))}<br>{html.escape(str((standby.get('selected') or {}).get('tag') or '-'))}</div></div>
    <div class="box"><div class="label">Health</div><div class="value">{html.escape(str((summary.get('last_health') or {}).get('status') or '-'))}</div></div>
  </section>
  <h2>URL probes</h2>
  <div class="table-wrap"><table><thead><tr><th>URL</th><th>Path</th><th>Status</th><th>Detail</th><th>Latency</th></tr></thead><tbody>{diagnostic_probe_rows(data.get('probes') or [])}</tbody></table></div>
  <h2>DNS probes</h2>
  <div class="table-wrap"><table><thead><tr><th>Name</th><th>Status</th><th>Detail</th><th>Latency</th></tr></thead><tbody>{dns_diagnostic_rows(data.get('dns') or {})}</tbody></table></div>
</main>
</body>
</html>"""
    return body.encode("utf-8")


def status_fragments(snapshot=None):
    snapshot = snapshot or status_snapshot()
    active_backend = snapshot.get("active_backend") or {}
    hot_standby = snapshot.get("hot_standby") or {}
    active_path = snapshot.get("active_path") or {}
    standby_observatory = snapshot.get("standby_observatory") or {}
    assets = snapshot.get("assets") or {}
    active_selected = active_path.get("selected") or {}
    standby_selected = standby_observatory.get("selected") or {}
    last_throughput = snapshot.get("last_throughput") or {}
    failover = snapshot.get("failover_state") or {}
    sources = snapshot.get("sources") or {}
    tested_live = snapshot.get("tested_live_candidates") or []
    health_checks = snapshot.get("health_checks") or {}
    counts = health_counts(health_checks)
    subscription_fetch = snapshot.get("subscription_fetch") or {}
    active_backend_candidate = active_backend.get("candidate") or {}
    hot_standby_candidate = hot_standby.get("candidate") or {}
    current = active_selected or active_backend_candidate or ((snapshot.get("active_pool") or [{}])[0])
    current_tag = active_path.get("selected_tag") or current.get("tag") or "-"
    current_endpoint = endpoint_text(current)
    current_transport = f"{current.get('network') or '-'} / {current.get('security') or '-'}"
    standby_display = standby_selected or hot_standby_candidate or ((snapshot.get("standby_pool") or [{}])[0])
    standby_endpoint = endpoint_text(standby_display) if standby_display else "-"
    active_pool_size = active_backend.get("pool_size") or active_path.get("pool_size") or len(snapshot.get("active_pool") or [])
    standby_pool_size = hot_standby.get("pool_size") or len(snapshot.get("standby_pool") or [])
    xray_chip_class = "ok" if active_backend.get("running", snapshot.get("xray_running")) else "fail"
    xray_chip_text = "running" if active_backend.get("running", snapshot.get("xray_running")) else "stopped"
    hot_chip_class = "ok" if hot_standby.get("healthy") else "warn" if hot_standby.get("running") else "fail"
    api_chip_class = "ok" if active_path.get("status") == "ok" else "warn"
    failover_note = failover.get("reason") or f"cooldown {failover.get('cooldown_remaining', 0)}s"
    switch_guard_remaining = int(failover.get("cooldown_remaining") or 0)
    if counts.get("fail", 0):
        ring_class = "fail"
        ring_value = counts.get("fail", 0)
        ring_label = "FAIL"
    elif counts.get("warn", 0):
        ring_class = "warn"
        ring_value = counts.get("warn", 0)
        ring_label = "WARN"
    elif counts.get("ok", 0):
        ring_class = "ok"
        ring_value = counts.get("ok", 0)
        ring_label = "OK"
    else:
        ring_class = "unknown"
        ring_value = counts.get("unknown", 0)
        ring_label = "UNKNOWN"
    server_preview = tested_live[:3]
    log_lines = "\n".join(
        f"{format_time(item.get('time'))} {item.get('line', '')}" for item in snapshot.get("logs", [])[-14:]
    )
    health_copy = "Traffic is served through the active VLESS path. Direct RU routing, split DNS, LAN VLESS inbound, SOCKS and HTTP proxy checks are tracked separately."
    return {
        "header-subline": f"{html.escape(timezone_label())} · refreshed {html.escape(format_time(snapshot.get('last_health_checks_at')))} · live update 15s",
        "system-card": f"""
      <div class="status-ring {ring_class}"><div><strong>{ring_value}</strong><span>{ring_label}</span></div></div>
      <div class="system-copy">
        <h2>{html.escape(health_title(counts))}</h2>
        <p>{html.escape(health_copy)}</p>
        <div class="metric-strip">
          <div class="mini-metric"><div class="label">Candidates</div><div class="metric-value">{snapshot.get('candidates_count', 0)}</div><div class="metric-note">extra {sources.get('extra', 0)} / sub {sources.get('subscription', 0)}</div></div>
          <div class="mini-metric"><div class="label">Tested live</div><div class="metric-value">{len(tested_live)}</div><div class="metric-note">ready for fallback</div></div>
          <div class="mini-metric"><div class="label">Throughput</div><div class="metric-value">{html.escape(throughput_text(last_throughput.get('kbps')))}</div><div class="metric-note">active path</div></div>
          <div class="mini-metric"><div class="label">Next test</div><div class="metric-value">{html.escape(short_time(snapshot.get('next_candidate_check_at')))}</div><div class="metric-note">random jitter</div></div>
          <div class="mini-metric"><div class="label">Subscription</div><div class="metric-value">{html.escape(short_time(snapshot.get('last_subscription_success_at')))}</div><div class="metric-note">last success</div></div>
          <div class="mini-metric"><div class="label">Failover</div><div class="metric-value">{html.escape(str(failover.get('state') or 'idle'))}</div><div class="metric-note">{html.escape(str(failover_note))}</div></div>
        </div>
      </div>""",
        "connection-card": f"""
      <div>
        <div class="label">Current connection</div>
        <div class="server-name">{html.escape(str(current_tag))}</div>
        <div class="server-endpoint">{html.escape(current_endpoint)} · {html.escape(current_transport)}</div>
      </div>
      <div class="chips">
        <span class="chip {xray_chip_class}">{xray_chip_text}</span>
        <span class="chip {api_chip_class}">api {html.escape(str(active_path.get('status') or '-'))}</span>
        <span class="chip blue">balancer {html.escape(str(active_path.get('strategy') or '-'))}</span>
        <span class="chip blue">active pool {active_pool_size}</span>
        <span class="chip {hot_chip_class}">hot {html.escape(str(hot_standby_candidate.get('tag') or '-'))}</span>
        <span class="chip {hot_chip_class}">hot pool {standby_pool_size}</span>
      </div>
      <div class="connection-grid">
        <div class="mini-metric"><div class="label">Score</div><div class="metric-value">{html.escape(score_value(current))}</div><div class="metric-note">{html.escape(score_reasons(current))}</div></div>
        <div class="mini-metric"><div class="label">Latency</div><div class="metric-value">{html.escape(format_metric(current.get('last_latency'), 's'))}</div><div class="metric-note">last OK</div></div>
        <div class="mini-metric"><div class="label">Hot standby</div><div class="metric-value">{html.escape(str(standby_display.get('tag') or '-'))}</div><div class="metric-note">{html.escape(standby_endpoint)}</div></div>
        <div class="mini-metric"><div class="label">Hot score</div><div class="metric-value">{html.escape(score_value(standby_display))}</div><div class="metric-note">{html.escape(score_reasons(standby_display))}</div></div>
        <div class="mini-metric"><div class="label">Switch guard</div><div class="metric-value">{switch_guard_remaining}s</div><div class="metric-note">cooldown window</div></div>
      </div>""",
        "health-panel": f"""
        <div class="panel-head">
          <div><h2>Health indicators</h2><div class="panel-subtitle">Separate subsystem status instead of one vague connection flag.</div></div>
          <div class="chips"><span class="chip ok">OK {counts.get('ok', 0)}</span><span class="chip warn">WARN {counts.get('warn', 0)}</span><span class="chip">UNKNOWN {counts.get('unknown', 0)}</span></div>
        </div>
        <div class="health-grid">{render_modern_health_grid(health_checks)}</div>""",
        "servers-panel": f"""
        <div class="panel-head">
          <div><h2>Servers</h2><div class="panel-subtitle">Preview of the live list. Full tables are available in separate tabs.</div></div>
          <div class="tabs"><a class="tab active" href="/servers/live">Live</a><a class="tab" href="/servers/all">All candidates</a></div>
        </div>
        <div class="table-wrap fill">
          <table><thead><tr><th>Score</th><th>Server</th><th>Endpoint</th><th>Transport</th><th>Latency</th><th>Last OK</th></tr></thead><tbody>{modern_server_rows(server_preview)}</tbody></table>
        </div>
        <div class="table-footer"><span>Showing top {len(server_preview)} of {len(tested_live)} live servers, sorted by score.</span><a class="chip blue" href="/servers/live">open full list</a></div>""",
        "routing-panel": f"""
        <div class="panel-head"><div><h2>Routing and assets</h2><div class="panel-subtitle">The parts that usually explain "works, but feels slow" problems.</div></div></div>
        <div class="info-grid">
          <div class="info-box"><div class="label">Geo assets</div><div class="metric-value">{html.escape(str((assets.get('last_status') or {}).get('status') or '-'))}</div><div class="metric-note">last downloaded {html.escape(short_time(assets.get('last_success_at')))}</div></div>
          <div class="info-box"><div class="label">Subscription fetch</div><div class="metric-value">{html.escape(str(subscription_fetch.get('last_method') or '-'))}</div><div class="metric-note">{html.escape(str(subscription_fetch.get('mode') or '-'))}</div></div>
          <div class="info-box"><div class="label">Direct RU routing</div><div class="metric-value">enabled</div><div class="metric-note">geoip:ru / geosite:category-ru</div></div>
        </div>""",
        "logs-panel": f"""
        <div class="panel-head"><div><h2>Recent events</h2><div class="panel-subtitle">Latest operational log entries.</div></div><a class="chip" href="/logs">plain logs</a></div>
        <pre class="logs">{html.escape(log_lines)}</pre>""",
    }


def render_status_html():
    snapshot = status_snapshot()
    active_backend = snapshot.get("active_backend") or {}
    hot_standby = snapshot.get("hot_standby") or {}
    active_path = snapshot.get("active_path") or {}
    standby_observatory = snapshot.get("standby_observatory") or {}
    assets = snapshot.get("assets") or {}
    active_selected = active_path.get("selected") or {}
    standby_selected = standby_observatory.get("selected") or {}
    last_throughput = snapshot.get("last_throughput") or {}
    failover = snapshot.get("failover_state") or {}
    sources = snapshot.get("sources") or {}
    tested_live = snapshot.get("tested_live_candidates") or []
    health_checks = snapshot.get("health_checks") or {}
    counts = health_counts(health_checks)
    subscription_fetch = snapshot.get("subscription_fetch") or {}
    active_backend_candidate = active_backend.get("candidate") or {}
    hot_standby_candidate = hot_standby.get("candidate") or {}
    current = active_selected or active_backend_candidate or ((snapshot.get("active_pool") or [{}])[0])
    current_tag = active_path.get("selected_tag") or current.get("tag") or "-"
    current_endpoint = endpoint_text(current)
    current_transport = f"{current.get('network') or '-'} / {current.get('security') or '-'}"
    standby_display = standby_selected or hot_standby_candidate or ((snapshot.get("standby_pool") or [{}])[0])
    standby_endpoint = endpoint_text(standby_display) if standby_display else "-"
    active_pool_size = active_backend.get("pool_size") or active_path.get("pool_size") or len(snapshot.get("active_pool") or [])
    standby_pool_size = hot_standby.get("pool_size") or len(snapshot.get("standby_pool") or [])
    xray_chip_class = "ok" if active_backend.get("running", snapshot.get("xray_running")) else "fail"
    xray_chip_text = "running" if active_backend.get("running", snapshot.get("xray_running")) else "stopped"
    hot_chip_class = "ok" if hot_standby.get("healthy") else "warn" if hot_standby.get("running") else "fail"
    api_chip_class = "ok" if active_path.get("status") == "ok" else "warn"
    failover_note = failover.get("reason") or f"cooldown {failover.get('cooldown_remaining', 0)}s"
    switch_guard_remaining = int(failover.get("cooldown_remaining") or 0)
    if counts.get("fail", 0):
        ring_class = "fail"
        ring_value = counts.get("fail", 0)
        ring_label = "FAIL"
    elif counts.get("warn", 0):
        ring_class = "warn"
        ring_value = counts.get("warn", 0)
        ring_label = "WARN"
    elif counts.get("ok", 0):
        ring_class = "ok"
        ring_value = counts.get("ok", 0)
        ring_label = "OK"
    else:
        ring_class = "unknown"
        ring_value = counts.get("unknown", 0)
        ring_label = "UNKNOWN"
    server_preview = tested_live[:3]
    log_lines = "\n".join(
        f"{format_time(item.get('time'))} {item.get('line', '')}" for item in snapshot.get("logs", [])[-14:]
    )
    health_copy = "Traffic is served through the active VLESS path. Direct RU routing, split DNS, LAN VLESS inbound, SOCKS and HTTP proxy checks are tracked separately."
    fragments = status_fragments(snapshot)
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>proxy-xray status</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #eef2f5;
      --panel: #ffffff;
      --panel-soft: #f8fafb;
      --text: #17212b;
      --muted: #61707f;
      --line: #dbe2e8;
      --line-strong: #c9d3dc;
      --green: #168052;
      --green-soft: #e8f7ef;
      --amber: #a86400;
      --amber-soft: #fff4dc;
      --red: #b9372d;
      --red-soft: #fff0ee;
      --blue: #2563a7;
      --blue-soft: #e9f2ff;
      --shadow: 0 10px 28px rgba(30, 45, 62, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: linear-gradient(180deg, #f7fafc 0, var(--bg) 310px), var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    main {{ width: min(1480px, calc(100% - 40px)); margin: 0 auto; padding: 24px 0 42px; }}
    .topbar {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 18px; }}
    .title-row {{ display: flex; align-items: center; gap: 12px; min-width: 0; }}
    .brand-mark {{ display: grid; place-items: center; width: 38px; height: 38px; border-radius: 8px; background: #122033; color: #fff; font-weight: 800; font-size: 17px; flex: 0 0 auto; }}
    h1 {{ margin: 0; font-size: 24px; line-height: 1.1; }}
    h2 {{ margin: 0; font-size: 18px; line-height: 1.2; }}
    .subline, .panel-subtitle, .metric-note {{ color: var(--muted); font-size: 13px; }}
    .subline {{ margin-top: 5px; }}
    .header-actions {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .icon-button, .text-button, .button-link {{ height: 34px; border: 1px solid var(--line-strong); background: rgba(255, 255, 255, 0.8); color: #273545; border-radius: 8px; font: inherit; font-size: 13px; text-decoration: none; display: inline-grid; place-items: center; }}
    .icon-button {{ width: 34px; font-weight: 800; }}
    .text-button, .button-link {{ padding: 0 12px; }}
    .hero {{ display: grid; grid-template-columns: minmax(0, 1.12fr) minmax(360px, 0.88fr); gap: 14px; margin-bottom: 14px; }}
    .system-card, .connection-card, .panel {{ background: rgba(255, 255, 255, 0.92); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); }}
    .system-card {{ padding: 18px; display: grid; grid-template-columns: auto 1fr; gap: 18px; align-items: center; }}
    .status-ring {{ width: 104px; height: 104px; border-radius: 50%; display: grid; place-items: center; position: relative; }}
    .status-ring.ok {{ background: conic-gradient(var(--green) 0 92%, #d9e2e9 92% 100%); }}
    .status-ring.warn {{ background: conic-gradient(var(--amber) 0 92%, #d9e2e9 92% 100%); }}
    .status-ring.fail {{ background: conic-gradient(var(--red) 0 92%, #d9e2e9 92% 100%); }}
    .status-ring.unknown {{ background: conic-gradient(#9aa3ad 0 92%, #d9e2e9 92% 100%); }}
    .status-ring::after {{ content: ""; position: absolute; inset: 10px; border-radius: 50%; background: #fff; }}
    .status-ring strong, .status-ring span {{ position: relative; z-index: 1; display: block; text-align: center; }}
    .status-ring strong {{ font-size: 22px; line-height: 1; }}
    .status-ring span {{ color: var(--muted); font-size: 12px; margin-top: 3px; }}
    .system-copy p {{ margin: 8px 0 0; color: var(--muted); font-size: 14px; max-width: 760px; }}
    .metric-strip {{ display: grid; grid-template-columns: repeat(3, minmax(120px, 1fr)); gap: 8px; margin-top: 16px; }}
    .mini-metric, .info-box {{ border: 1px solid var(--line); background: var(--panel-soft); border-radius: 8px; padding: 10px; min-height: 70px; }}
    .label {{ color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 750; line-height: 1.2; }}
    .metric-value {{ margin-top: 6px; font-size: 20px; line-height: 1.1; font-weight: 750; overflow-wrap: anywhere; }}
    .connection-card {{ padding: 18px; display: flex; flex-direction: column; gap: 14px; }}
    .server-name {{ font-size: 25px; line-height: 1.08; font-weight: 800; overflow-wrap: anywhere; }}
    .server-endpoint {{ color: var(--muted); font-size: 14px; margin-top: 5px; overflow-wrap: anywhere; }}
    .chips {{ display: flex; gap: 7px; flex-wrap: wrap; }}
    .chip {{ display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 9px; border-radius: 999px; font-size: 12px; border: 1px solid var(--line); color: #263442; background: #fff; white-space: nowrap; text-decoration: none; }}
    .chip.ok {{ color: var(--green); background: var(--green-soft); border-color: #b8e2ca; }}
    .chip.warn {{ color: var(--amber); background: var(--amber-soft); border-color: #efd08f; }}
    .chip.fail {{ color: var(--red); background: var(--red-soft); border-color: #efb4ae; }}
    .chip.blue {{ color: var(--blue); background: var(--blue-soft); border-color: #c6dcf6; }}
    .connection-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; margin-top: auto; }}
    .section-grid, .lower-grid {{ display: grid; grid-template-columns: minmax(430px, 0.86fr) minmax(0, 1.14fr); gap: 14px; align-items: stretch; }}
    .panel {{ padding: 16px; margin-bottom: 14px; }}
    .panel.fill, .panel.stretch {{ height: calc(100% - 14px); }}
    .panel.fill {{ display: flex; flex-direction: column; }}
    .panel-head {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; margin-bottom: 12px; }}
    .health-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
    .health-item {{ min-height: 88px; border: 1px solid var(--line); border-left-width: 5px; border-radius: 8px; background: var(--panel-soft); padding: 10px 11px; }}
    .health-item.ok {{ border-left-color: var(--green); }}
    .health-item.warn {{ border-left-color: var(--amber); }}
    .health-item.fail {{ border-left-color: var(--red); }}
    .health-item.unknown {{ border-left-color: #9aa3ad; }}
    .health-top {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
    .health-name {{ font-weight: 750; font-size: 14px; line-height: 1.2; }}
    .health-state {{ font-size: 11px; font-weight: 800; }}
    .health-state.ok {{ color: var(--green); }}
    .health-state.warn {{ color: var(--amber); }}
    .health-state.fail {{ color: var(--red); }}
    .health-detail {{ margin-top: 9px; color: #334252; font-size: 13px; line-height: 1.35; overflow-wrap: anywhere; }}
    .health-meta {{ margin-top: 5px; color: var(--muted); font-size: 12px; }}
    .info-grid {{ display: grid; grid-template-columns: 1fr; gap: 10px; }}
    .info-box {{ min-height: 112px; }}
    .info-box .metric-value {{ font-size: 17px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; background: #fff; }}
    .table-wrap.fill {{ flex: 1; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 820px; }}
    .table-wrap.fill table {{ height: 100%; }}
    .table-wrap.fill tbody tr {{ height: 33.333%; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e9eef2; font-size: 13px; vertical-align: top; }}
    th {{ color: #51606f; background: #f4f7f9; font-size: 11px; text-transform: uppercase; font-weight: 800; }}
    tr:last-child td {{ border-bottom: 0; }}
    .tabs {{ display: inline-flex; padding: 3px; gap: 3px; border: 1px solid var(--line); border-radius: 8px; background: #f3f6f8; }}
    .tab {{ height: 28px; padding: 0 10px; border-radius: 6px; color: #536273; font-size: 12px; font-weight: 700; text-decoration: none; display: inline-grid; place-items: center; }}
    .tab.active {{ color: #182331; background: #fff; box-shadow: 0 1px 4px rgba(22, 36, 51, 0.12); }}
    .table-footer {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; color: var(--muted); font-size: 12px; margin-top: 10px; }}
    .score-stack {{ min-width: 210px; max-width: 360px; }}
    .score {{ display: flex; align-items: center; gap: 8px; }}
    .score-bar {{ flex: 1; height: 7px; border-radius: 999px; background: #dce4eb; overflow: hidden; }}
    .score-bar span {{ display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--green), #4b9e8a); }}
    .score-reasons {{ margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.35; }}
    .logs {{ margin: 0; min-height: 332px; height: calc(100% - 70px); overflow: auto; border-radius: 8px; background: #101820; color: #dbe4ed; padding: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; line-height: 1.55; white-space: pre-wrap; }}
    @media (max-width: 980px) {{
      main {{ width: min(100% - 24px, 1480px); }}
      .hero, .section-grid, .lower-grid {{ grid-template-columns: 1fr; }}
      .metric-strip {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 640px) {{
      .topbar, .panel-head, .system-card {{ display: block; }}
      .header-actions {{ justify-content: flex-start; margin-top: 12px; }}
      .status-ring {{ width: 90px; height: 90px; margin-bottom: 14px; }}
      .metric-strip, .connection-grid, .health-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main>
  <header class="topbar">
    <div class="title-row">
      <div class="brand-mark">XR</div>
      <div>
        <h1>proxy-xray status</h1>
        <div class="subline" data-fragment="header-subline">{fragments["header-subline"]}</div>
      </div>
    </div>
    <div class="header-actions">
      <a class="icon-button" href="/dashboard-classic" title="Refresh">R</a>
      <a class="text-button" href="/" title="Main dashboard">Main</a>
      <a class="icon-button" href="/json" title="Open JSON">J</a>
      <a class="icon-button" href="/logs" title="Open logs">L</a>
      <a class="icon-button" href="/diagnostics" title="Diagnostics">D</a>
      <a class="icon-button" href="/client" title="LAN VLESS client QR">Q</a>
    </div>
  </header>

  <section class="hero">
    <div class="system-card" data-fragment="system-card">
{fragments["system-card"]}
    </div>

    <aside class="connection-card" data-fragment="connection-card">
{fragments["connection-card"]}
    </aside>
  </section>

  <section class="section-grid">
    <div>
      <section class="panel" data-fragment="health-panel">
{fragments["health-panel"]}
      </section>
    </div>
    <div>
      <section class="panel fill" data-fragment="servers-panel">
{fragments["servers-panel"]}
      </section>
    </div>
  </section>

  <section class="lower-grid">
    <div>
      <section class="panel stretch" data-fragment="routing-panel">
{fragments["routing-panel"]}
      </section>
    </div>
    <div>
      <section class="panel stretch" data-fragment="logs-panel">
{fragments["logs-panel"]}
      </section>
    </div>
  </section>
</main>
<script>
(() => {{
  const intervalMs = 15000;
  const keys = [
    "header-subline",
    "system-card",
    "connection-card",
    "health-panel",
    "servers-panel",
    "routing-panel",
    "logs-panel",
  ];

  async function refreshFragments() {{
    try {{
      const response = await fetch("/fragments/status", {{ cache: "no-store" }});
      if (!response.ok) {{
        throw new Error(`HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      const fragments = payload.fragments || {{}};
      for (const key of keys) {{
        const node = document.querySelector(`[data-fragment="${{key}}"]`);
        if (node && Object.prototype.hasOwnProperty.call(fragments, key)) {{
          const next = fragments[key];
          if (node.innerHTML !== next) {{
            node.innerHTML = next;
          }}
        }}
      }}
    }} catch (error) {{
      console.warn("status fragment refresh failed", error);
    }}
  }}

  window.setInterval(refreshFragments, intervalMs);
  document.addEventListener("visibilitychange", () => {{
    if (!document.hidden) refreshFragments();
  }});
}})();
</script>
</body>
</html>"""
    return body.encode("utf-8")


def render_dashboard_v5_html():
    fragments = dashboard_v5_fragments()
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>proxy-xray status</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #edf2f6;
      --panel: #ffffff;
      --panel-soft: #f7f9fb;
      --text: #111b27;
      --muted: #657485;
      --line: #d7e0e8;
      --line-strong: #c4d0dc;
      --green: #168052;
      --green-soft: #e8f7ef;
      --amber: #a86400;
      --amber-soft: #fff4dc;
      --red: #b9372d;
      --red-soft: #fff0ee;
      --blue: #2563a7;
      --blue-soft: #e9f2ff;
      --shadow: 0 18px 40px rgba(28, 44, 60, 0.09);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: linear-gradient(180deg, #f8fbfd 0, var(--bg) 360px), var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }}
    main {{ width: min(1560px, calc(100% - 36px)); margin: 0 auto; padding: 22px 0 40px; }}
    .v5-topbar {{ display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 14px; }}
    .v5-brand {{ display: flex; align-items: center; gap: 12px; min-width: 0; }}
    .v5-mark {{ display: grid; place-items: center; width: 40px; height: 40px; border-radius: 8px; background: #111b27; color: #fff; font-size: 18px; font-weight: 850; }}
    h1, h2, h3, p {{ margin: 0; }}
    h1 {{ font-size: 25px; line-height: 1.05; }}
    .v5-subline, .v5-muted, small {{ color: var(--muted); }}
    .v5-subline {{ margin-top: 4px; font-size: 13px; }}
    .v5-actions {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .v5-button {{ min-height: 34px; border: 1px solid var(--line-strong); background: rgba(255,255,255,.82); color: #273545; border-radius: 8px; padding: 7px 11px; font-size: 13px; font-weight: 750; text-decoration: none; display: inline-grid; place-items: center; }}
    .v5-button.primary {{ color: var(--blue); background: var(--blue-soft); border-color: #c6dcf6; }}
    .v5-shell {{ display: grid; grid-template-columns: minmax(310px, .74fr) minmax(520px, 1.25fr) minmax(330px, .8fr); gap: 14px; align-items: start; }}
    .v5-panel {{ background: rgba(255,255,255,.94); border: 1px solid var(--line); border-radius: 8px; box-shadow: var(--shadow); padding: 14px; }}
    .v5-stack {{ display: grid; gap: 14px; }}
    .v5-overview {{ min-height: 172px; }}
    .v5-state {{ display: flex; align-items: center; gap: 13px; margin-bottom: 14px; }}
    .v5-state-dot {{ width: 54px; height: 54px; border-radius: 50%; flex: 0 0 auto; box-shadow: inset 0 0 0 10px rgba(255,255,255,.72); }}
    .v5-state.ok .v5-state-dot {{ background: var(--green); }}
    .v5-state.warn .v5-state-dot {{ background: var(--amber); }}
    .v5-state.fail .v5-state-dot {{ background: var(--red); }}
    .v5-state.unknown .v5-state-dot {{ background: #8a96a3; }}
    .v5-state h2 {{ font-size: 22px; line-height: 1.1; }}
    .v5-state p {{ margin-top: 5px; color: var(--muted); font-size: 13px; line-height: 1.35; }}
    .v5-kpi-row, .v5-current-grid, .v5-routing-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 8px; }}
    .v5-kpi, .v5-current-grid > div, .v5-routing-grid > div {{ min-height: 78px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel-soft); padding: 10px; }}
    .v5-kpi span, .v5-current-grid span, .v5-routing-grid span {{ display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; font-weight: 800; }}
    .v5-kpi strong, .v5-current-grid strong, .v5-routing-grid strong {{ display: block; margin-top: 5px; font-size: 21px; line-height: 1.05; overflow-wrap: anywhere; }}
    .v5-kpi small, .v5-current-grid small, .v5-routing-grid small {{ display: block; margin-top: 5px; font-size: 12px; line-height: 1.25; overflow-wrap: anywhere; }}
    .v5-panel-title {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 11px; }}
    .v5-panel-title span {{ font-size: 13px; text-transform: uppercase; color: #5d6b7a; font-weight: 850; }}
    .v5-panel-title a {{ color: var(--blue); text-decoration: none; font-size: 12px; font-weight: 750; }}
    .v5-current-name {{ font-size: 31px; line-height: 1.02; font-weight: 850; overflow-wrap: anywhere; }}
    .v5-current-endpoint {{ margin-top: 5px; color: var(--muted); font-size: 14px; overflow-wrap: anywhere; }}
    .v5-chip-row {{ display: flex; gap: 7px; flex-wrap: wrap; margin: 12px 0; }}
    .v5-chip {{ display: inline-flex; align-items: center; min-height: 25px; padding: 4px 9px; border-radius: 999px; border: 1px solid var(--line); background: #fff; color: #293746; font-size: 12px; font-weight: 700; }}
    .v5-chip.ok {{ color: var(--green); background: var(--green-soft); border-color: #b8e2ca; }}
    .v5-chip.warn {{ color: var(--amber); background: var(--amber-soft); border-color: #efd08f; }}
    .v5-chip.fail {{ color: var(--red); background: var(--red-soft); border-color: #efb4ae; }}
    .v5-health-list, .v5-server-list, .v5-events {{ display: grid; gap: 8px; }}
    .v5-health-row, .v5-server-card, .v5-event {{ border: 1px solid var(--line); border-radius: 8px; background: var(--panel-soft); padding: 9px 10px; }}
    .v5-health-row {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 58px; }}
    .v5-health-main {{ display: grid; grid-template-columns: auto 1fr; column-gap: 8px; row-gap: 2px; min-width: 0; }}
    .v5-dot {{ width: 9px; height: 9px; border-radius: 50%; margin-top: 5px; }}
    .v5-health-row.ok .v5-dot {{ background: var(--green); }}
    .v5-health-row.warn .v5-dot {{ background: var(--amber); }}
    .v5-health-row.fail .v5-dot {{ background: var(--red); }}
    .v5-health-row.unknown .v5-dot {{ background: #8a96a3; }}
    .v5-health-main strong {{ font-size: 14px; overflow-wrap: anywhere; }}
    .v5-health-main small {{ grid-column: 2; font-size: 12px; overflow-wrap: anywhere; }}
    .v5-health-side {{ text-align: right; flex: 0 0 auto; }}
    .v5-health-side span {{ display: block; font-size: 11px; font-weight: 850; }}
    .v5-health-row.ok .v5-health-side span {{ color: var(--green); }}
    .v5-health-row.warn .v5-health-side span {{ color: var(--amber); }}
    .v5-health-row.fail .v5-health-side span {{ color: var(--red); }}
    .v5-health-side small {{ display: block; margin-top: 3px; font-size: 11px; }}
    .v5-pool-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; }}
    h3 {{ color: #4c5d6f; font-size: 12px; text-transform: uppercase; margin-bottom: 8px; }}
    .v5-server-card {{ display: grid; grid-template-columns: minmax(98px, 1fr) minmax(145px, .9fr); gap: 8px; align-items: start; min-height: 72px; }}
    .v5-server-tag {{ font-size: 16px; font-weight: 850; overflow-wrap: break-word; word-break: normal; }}
    .v5-muted {{ font-size: 12px; line-height: 1.3; overflow-wrap: break-word; word-break: normal; }}
    .v5-server-meta {{ text-align: right; display: grid; gap: 3px; }}
    .v5-server-meta strong {{ font-size: 17px; }}
    .v5-server-meta span {{ color: var(--muted); font-size: 12px; line-height: 1.25; overflow-wrap: anywhere; }}
    .v5-pool-grid .v5-server-card {{ grid-template-columns: 1fr; gap: 4px; min-height: 92px; }}
    .v5-pool-grid .v5-server-meta {{ text-align: left; }}
    .v5-event {{ display: grid; grid-template-columns: 64px minmax(0,1fr); gap: 10px; position: relative; }}
    .v5-event::before {{ content: ""; position: absolute; left: 76px; top: 14px; width: 8px; height: 8px; border-radius: 50%; background: #94a1ad; }}
    .v5-event.ok::before {{ background: var(--green); }}
    .v5-event.warn::before {{ background: var(--amber); }}
    .v5-event.fail::before {{ background: var(--red); }}
    .v5-event time {{ color: var(--muted); font-size: 12px; }}
    .v5-event span {{ font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow-wrap: anywhere; }}
    .v5-empty {{ border: 1px dashed var(--line-strong); border-radius: 8px; min-height: 58px; display: grid; place-items: center; color: var(--muted); font-size: 13px; padding: 10px; text-align: center; }}
    @media (max-width: 1220px) {{ .v5-shell {{ grid-template-columns: 1fr 1fr; }} .v5-right {{ grid-column: 1 / -1; display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }} }}
    @media (max-width: 820px) {{
      main {{ width: min(100% - 24px, 1560px); }}
      .v5-topbar, .v5-shell, .v5-right, .v5-pool-grid {{ display: block; }}
      .v5-actions {{ justify-content: flex-start; margin-top: 12px; }}
      .v5-panel {{ margin-bottom: 14px; }}
      .v5-server-card {{ grid-template-columns: 1fr; }}
      .v5-server-meta {{ text-align: left; }}
    }}
    @media (max-width: 560px) {{ .v5-kpi-row, .v5-current-grid, .v5-routing-grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <header class="v5-topbar">
    <div class="v5-brand">
      <div class="v5-mark">XR</div>
      <div>
        <h1>proxy-xray status</h1>
        <div class="v5-subline" data-fragment="v5-subline">{fragments["v5-subline"]}</div>
      </div>
    </div>
    <nav class="v5-actions">
      <a class="v5-button primary" href="/dashboard-classic">Classic dashboard</a>
      <a class="v5-button" href="/servers/live">Live</a>
      <a class="v5-button" href="/diagnostics">Diagnostics</a>
      <a class="v5-button" href="/client">QR</a>
    </nav>
  </header>

  <section class="v5-shell">
    <aside class="v5-stack">
      <section class="v5-panel v5-overview" data-fragment="v5-overview">
{fragments["v5-overview"]}
      </section>
      <section class="v5-panel" data-fragment="v5-health">
{fragments["v5-health"]}
      </section>
    </aside>

    <section class="v5-stack">
      <section class="v5-panel" data-fragment="v5-current">
{fragments["v5-current"]}
      </section>
      <section class="v5-panel" data-fragment="v5-pools">
{fragments["v5-pools"]}
      </section>
      <section class="v5-panel" data-fragment="v5-servers">
{fragments["v5-servers"]}
      </section>
    </section>

    <aside class="v5-stack v5-right">
      <section class="v5-panel" data-fragment="v5-routing">
{fragments["v5-routing"]}
      </section>
      <section class="v5-panel" data-fragment="v5-events">
{fragments["v5-events"]}
      </section>
    </aside>
  </section>
</main>
<script>
(() => {{
  const intervalMs = 15000;
  const keys = [
    "v5-subline",
    "v5-overview",
    "v5-current",
    "v5-health",
    "v5-pools",
    "v5-servers",
    "v5-routing",
    "v5-events",
  ];

  async function refreshFragments() {{
    try {{
      const response = await fetch("/fragments/dashboard-v5", {{ cache: "no-store" }});
      if (!response.ok) {{
        throw new Error(`HTTP ${{response.status}}`);
      }}
      const payload = await response.json();
      const fragments = payload.fragments || {{}};
      for (const key of keys) {{
        const node = document.querySelector(`[data-fragment="${{key}}"]`);
        if (node && Object.prototype.hasOwnProperty.call(fragments, key)) {{
          const next = fragments[key];
          if (node.innerHTML !== next) {{
            node.innerHTML = next;
          }}
        }}
      }}
    }} catch (error) {{
      console.warn("dashboard v5 fragment refresh failed", error);
    }}
  }}

  window.setInterval(refreshFragments, intervalMs);
  document.addEventListener("visibilitychange", () => {{
    if (!document.hidden) refreshFragments();
  }});
}})();
</script>
</body>
</html>"""
    return body.encode("utf-8")


def render_servers_html(kind):
    snapshot = status_snapshot()
    tested_live = snapshot.get("tested_live_candidates") or []
    all_candidates = snapshot.get("candidates") or []
    selected = tested_live if kind == "live" else all_candidates
    title = "Live servers" if kind == "live" else "All candidates"
    active_live = "active" if kind == "live" else ""
    active_all = "active" if kind == "all" else ""
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} · proxy-xray</title>
  <style>
    body {{ margin: 0; background: #eef2f5; color: #17212b; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(1480px, calc(100% - 40px)); margin: 0 auto; padding: 24px 0 42px; }}
    .topbar {{ display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 18px; }}
    h1 {{ margin: 0; font-size: 24px; }}
    .muted {{ color: #61707f; font-size: 13px; margin-top: 5px; }}
    .actions, .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    a {{ color: #2563a7; }}
    .button, .tab {{ min-height: 34px; border: 1px solid #c9d3dc; background: #fff; color: #273545; border-radius: 8px; padding: 7px 12px; font-size: 13px; text-decoration: none; display: inline-grid; place-items: center; }}
    .tab.active {{ color: #17212b; font-weight: 800; background: #e9f2ff; border-color: #c6dcf6; }}
    .panel {{ background: #fff; border: 1px solid #dbe2e8; border-radius: 8px; box-shadow: 0 10px 28px rgba(30, 45, 62, 0.08); padding: 16px; }}
    .panel-head {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; margin-bottom: 12px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid #dbe2e8; border-radius: 8px; background: #fff; }}
    table {{ width: 100%; min-width: 980px; border-collapse: collapse; }}
    th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #e9eef2; font-size: 13px; vertical-align: top; }}
    th {{ color: #51606f; background: #f4f7f9; font-size: 11px; text-transform: uppercase; font-weight: 800; }}
    .metric-note {{ color: #61707f; font-size: 12px; }}
    .score-stack {{ min-width: 210px; max-width: 360px; }}
    .score {{ display: flex; align-items: center; gap: 8px; }}
    .score-bar {{ flex: 1; height: 7px; border-radius: 999px; background: #dce4eb; overflow: hidden; }}
    .score-bar span {{ display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #168052, #4b9e8a); }}
    .score-reasons {{ margin-top: 5px; color: #61707f; font-size: 12px; line-height: 1.35; }}
  </style>
</head>
<body>
<main>
  <div class="topbar">
    <div>
      <h1>{html.escape(title)}</h1>
      <div class="muted">{len(selected)} rows · {html.escape(timezone_label())}</div>
    </div>
    <div class="actions"><a class="button" href="/">Status</a><a class="button" href="/json">JSON</a><a class="button" href="/diagnostics">Diagnostics</a></div>
  </div>
  <section class="panel">
    <div class="panel-head">
      <div class="tabs"><a class="tab {active_live}" href="/servers/live">Live</a><a class="tab {active_all}" href="/servers/all">All candidates</a></div>
      <div class="muted">Sorted by fallback score where available.</div>
    </div>
    <div class="table-wrap">
      <table><thead><tr><th>Score</th><th>Server</th><th>Endpoint</th><th>Transport</th><th>Latency</th><th>Last OK</th></tr></thead><tbody>{modern_server_rows(selected)}</tbody></table>
    </div>
  </section>
</main>
</body>
</html>"""
    return body.encode("utf-8")


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

    def do_GET(self):
        args = STATUS_ARGS
        if self.path in ("/", "/status", "/dashboard-v5", "/v5"):
            self.send_bytes(render_dashboard_v5_html(), "text/html; charset=utf-8")
            return
        if self.path in ("/dashboard-classic", "/classic"):
            self.send_bytes(render_status_html(), "text/html; charset=utf-8")
            return
        if self.path == "/fragments/status":
            data = json.dumps(
                {"time": time.time(), "fragments": status_fragments()},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_bytes(data, "application/json; charset=utf-8")
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
