import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .status import LOG_BUFFER, log, status_snapshot


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


def render_status_html():
    snapshot = status_snapshot()
    fallback = snapshot.get("fallback") or {}
    standby = snapshot.get("standby") or {}
    active_backend = snapshot.get("active_backend") or {}
    hot_standby = snapshot.get("hot_standby") or {}
    active_path = snapshot.get("active_path") or {}
    assets = snapshot.get("assets") or {}
    active_selected = active_path.get("selected") or {}
    last_throughput = snapshot.get("last_throughput") or {}
    sources = snapshot.get("sources") or {}
    tested_live = snapshot.get("tested_live_candidates") or []
    health_checks = snapshot.get("health_checks") or {}
    counts = health_counts(health_checks)
    subscription_fetch = snapshot.get("subscription_fetch") or {}
    active_backend_candidate = active_backend.get("candidate") or {}
    hot_standby_candidate = hot_standby.get("candidate") or {}
    current = active_backend_candidate or active_selected or fallback
    current_tag = active_path.get("selected_tag") or current.get("tag") or "-"
    current_endpoint = endpoint_text(current)
    current_transport = f"{current.get('network') or '-'} / {current.get('security') or '-'}"
    standby_display = hot_standby_candidate or standby
    standby_endpoint = endpoint_text(standby_display) if standby_display else "-"
    xray_chip_class = "ok" if active_backend.get("running", snapshot.get("xray_running")) else "fail"
    xray_chip_text = "running" if active_backend.get("running", snapshot.get("xray_running")) else "stopped"
    hot_chip_class = "ok" if hot_standby.get("healthy") else "warn" if hot_standby.get("running") else "fail"
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
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
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
        <div class="subline">{html.escape(timezone_label())} · refreshed {html.escape(format_time(snapshot.get('last_health_checks_at')))} · auto-refresh 15s</div>
      </div>
    </div>
    <div class="header-actions">
      <a class="icon-button" href="/" title="Refresh">R</a>
      <a class="icon-button" href="/json" title="Open JSON">J</a>
      <a class="icon-button" href="/logs" title="Open logs">L</a>
      <a class="button-link" href="/legacy">Old version</a>
    </div>
  </header>

  <section class="hero">
    <div class="system-card">
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
          <div class="mini-metric"><div class="label">Switch guard</div><div class="metric-value">{html.escape(seconds_left(snapshot.get('switch_cooldown_until')))}</div><div class="metric-note">quarantine {snapshot.get('quarantine_count', 0)}</div></div>
        </div>
      </div>
    </div>

    <aside class="connection-card">
      <div>
        <div class="label">Current connection</div>
        <div class="server-name">{html.escape(str(current_tag))}</div>
        <div class="server-endpoint">{html.escape(current_endpoint)} · {html.escape(current_transport)}</div>
      </div>
      <div class="chips">
        <span class="chip {xray_chip_class}">{xray_chip_text}</span>
        <span class="chip blue">balancer {html.escape(str(active_path.get('strategy') or '-'))}</span>
        <span class="chip {hot_chip_class}">hot {html.escape(str(hot_standby_candidate.get('tag') or '-'))}</span>
      </div>
      <div class="connection-grid">
        <div class="mini-metric"><div class="label">Score</div><div class="metric-value">{html.escape(score_value(current))}</div><div class="metric-note">{html.escape(score_reasons(current))}</div></div>
        <div class="mini-metric"><div class="label">Latency</div><div class="metric-value">{html.escape(format_metric(current.get('last_latency'), 's'))}</div><div class="metric-note">last OK</div></div>
        <div class="mini-metric"><div class="label">Hot standby</div><div class="metric-value">{html.escape(str(hot_standby_candidate.get('tag') or '-'))}</div><div class="metric-note">{html.escape(standby_endpoint)}</div></div>
        <div class="mini-metric"><div class="label">Hot score</div><div class="metric-value">{html.escape(score_value(hot_standby_candidate))}</div><div class="metric-note">{html.escape(score_reasons(hot_standby_candidate))}</div></div>
      </div>
    </aside>
  </section>

  <section class="section-grid">
    <div>
      <section class="panel">
        <div class="panel-head">
          <div><h2>Health indicators</h2><div class="panel-subtitle">Separate subsystem status instead of one vague connection flag.</div></div>
          <div class="chips"><span class="chip ok">OK {counts.get('ok', 0)}</span><span class="chip warn">WARN {counts.get('warn', 0)}</span><span class="chip">UNKNOWN {counts.get('unknown', 0)}</span></div>
        </div>
        <div class="health-grid">{render_modern_health_grid(health_checks)}</div>
      </section>
    </div>
    <div>
      <section class="panel fill">
        <div class="panel-head">
          <div><h2>Servers</h2><div class="panel-subtitle">Preview of the live list. Full tables are available in separate tabs.</div></div>
          <div class="tabs"><a class="tab active" href="/servers/live">Live</a><a class="tab" href="/servers/all">All candidates</a></div>
        </div>
        <div class="table-wrap fill">
          <table><thead><tr><th>Score</th><th>Server</th><th>Endpoint</th><th>Transport</th><th>Latency</th><th>Last OK</th></tr></thead><tbody>{modern_server_rows(server_preview)}</tbody></table>
        </div>
        <div class="table-footer"><span>Showing top {len(server_preview)} of {len(tested_live)} live servers, sorted by score.</span><a class="chip blue" href="/servers/live">open full list</a></div>
      </section>
    </div>
  </section>

  <section class="lower-grid">
    <div>
      <section class="panel stretch">
        <div class="panel-head"><div><h2>Routing and assets</h2><div class="panel-subtitle">The parts that usually explain "works, but feels slow" problems.</div></div></div>
        <div class="info-grid">
          <div class="info-box"><div class="label">Geo assets</div><div class="metric-value">{html.escape(str((assets.get('last_status') or {}).get('status') or '-'))}</div><div class="metric-note">last downloaded {html.escape(short_time(assets.get('last_success_at')))}</div></div>
          <div class="info-box"><div class="label">Subscription fetch</div><div class="metric-value">{html.escape(str(subscription_fetch.get('last_method') or '-'))}</div><div class="metric-note">{html.escape(str(subscription_fetch.get('mode') or '-'))}</div></div>
          <div class="info-box"><div class="label">Direct RU routing</div><div class="metric-value">enabled</div><div class="metric-note">geoip:ru / geosite:category-ru</div></div>
        </div>
      </section>
    </div>
    <div>
      <section class="panel stretch">
        <div class="panel-head"><div><h2>Recent events</h2><div class="panel-subtitle">Latest operational log entries.</div></div><a class="chip" href="/logs">plain logs</a></div>
        <pre class="logs">{html.escape(log_lines)}</pre>
      </section>
    </div>
  </section>
</main>
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
    <div class="actions"><a class="button" href="/">Status</a><a class="button" href="/legacy">Old version</a><a class="button" href="/json">JSON</a></div>
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


def render_legacy_status_html():
    snapshot = status_snapshot()
    fallback = snapshot.get("fallback") or {}
    active_path = snapshot.get("active_path") or {}
    assets = snapshot.get("assets") or {}
    active_selected = active_path.get("selected") or {}
    active_fallback = active_path.get("fallback") or fallback
    last_health = snapshot.get("last_health") or {}
    last_throughput = snapshot.get("last_throughput") or {}
    last_candidate_check = snapshot.get("last_candidate_check") or {}
    sources = snapshot.get("sources") or {}
    tested_live = snapshot.get("tested_live_candidates") or []
    all_candidates = snapshot.get("candidates") or []
    health_checks = snapshot.get("health_checks") or {}
    counts = health_counts(health_checks)
    subscription_fetch = snapshot.get("subscription_fetch") or {}
    log_lines = "\n".join(
        f"{format_time(item.get('time'))} {item.get('line', '')}" for item in snapshot.get("logs", [])[-120:]
    )
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>proxy-xray status</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #f5f6f7; color: #17202a; }}
    main {{ margin: 0 auto; max-width: 1480px; padding: 22px; }}
    h1 {{ font-size: 24px; margin: 0; }}
    h2 {{ font-size: 18px; margin: 26px 0 10px; }}
    .topbar {{ display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; margin-bottom: 16px; }}
    .links {{ font-size: 13px; white-space: nowrap; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(176px, 1fr)); gap: 10px; }}
    .box {{ background: #fff; border: 1px solid #d9dde3; border-radius: 6px; padding: 12px; }}
    .label {{ color: #687382; font-size: 12px; text-transform: uppercase; }}
    .value {{ font-size: 18px; margin-top: 4px; overflow-wrap: anywhere; }}
    .muted {{ color: #687382; font-size: 13px; }}
    .health-summary {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0 12px; }}
    .pill {{ border-radius: 999px; padding: 3px 9px; font-size: 12px; border: 1px solid #d9dde3; background: #fff; }}
    .pill.ok {{ color: #17633a; border-color: #b9dfca; background: #effaf3; }}
    .pill.warn {{ color: #7a4b00; border-color: #f1d39b; background: #fff7e6; }}
    .pill.fail {{ color: #8c1d18; border-color: #efb4ae; background: #fff0ee; }}
    .pill.unknown {{ color: #56606c; border-color: #d9dde3; background: #f8f9fa; }}
    .health-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }}
    .health-card {{ background: #fff; border: 1px solid #d9dde3; border-left-width: 5px; border-radius: 6px; padding: 11px 12px; min-height: 82px; }}
    .health-card.ok {{ border-left-color: #2e8b57; }}
    .health-card.warn {{ border-left-color: #d18b00; }}
    .health-card.fail {{ border-left-color: #d6453d; }}
    .health-card.unknown {{ border-left-color: #9aa3ad; }}
    .health-top {{ display: flex; justify-content: space-between; gap: 8px; font-size: 13px; }}
    .health-top strong {{ font-size: 12px; letter-spacing: 0; }}
    .health-detail {{ color: #384250; font-size: 13px; margin-top: 8px; overflow-wrap: anywhere; }}
    .health-latency {{ color: #687382; font-size: 12px; margin-top: 4px; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid #d9dde3; background: #fff; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d9dde3; }}
    .table-wrap table {{ border: 0; min-width: 900px; }}
    th, td {{ text-align: left; padding: 7px 9px; border-bottom: 1px solid #e6e9ee; font-size: 13px; vertical-align: top; }}
    th {{ background: #eef1f4; }}
    pre {{ background: #111820; color: #d8dee9; padding: 12px; border-radius: 6px; overflow: auto; max-height: 520px; }}
    a {{ color: #005ea8; }}
    @media (max-width: 720px) {{
      main {{ padding: 14px; }}
      .topbar {{ align-items: flex-start; flex-direction: column; }}
      .links {{ white-space: normal; }}
    }}
  </style>
</head>
<body>
<main>
  <div class="topbar">
    <div>
      <h1>proxy-xray status</h1>
      <div class="muted">Auto-refresh every 15 seconds · {html.escape(timezone_label())}</div>
    </div>
    <div class="links"><a href="/json">JSON</a> · <a href="/logs">Plain logs</a></div>
  </div>
  <h2>Overview</h2>
  <div class="grid">
    <div class="box"><div class="label">Xray</div><div class="value">{'running' if snapshot.get('xray_running') else 'stopped'}</div></div>
    <div class="box"><div class="label">Candidates</div><div class="value">{snapshot.get('candidates_count', 0)}</div></div>
    <div class="box"><div class="label">Tested Live</div><div class="value">{len(tested_live)}</div></div>
    <div class="box"><div class="label">Active Path</div><div class="value">{html.escape(str(active_path.get('selected_tag') or active_path.get('balancer') or '-'))}<br>{html.escape(endpoint_text(active_selected) if active_selected else str(active_path.get('strategy') or ''))}</div></div>
    <div class="box"><div class="label">Subscription OK</div><div class="value">{html.escape(format_time(snapshot.get('last_subscription_success_at')))}</div></div>
    <div class="box"><div class="label">Geo Assets OK</div><div class="value">{html.escape(format_time(assets.get('last_success_at')))}<br>{html.escape(str((assets.get('last_status') or {}).get('status') or '-'))}</div></div>
    <div class="box"><div class="label">Subscription Fetch</div><div class="value">{html.escape(str(subscription_fetch.get('last_method') or '-'))}<br>{html.escape(str(subscription_fetch.get('mode') or '-'))}</div></div>
    <div class="box"><div class="label">Next Test</div><div class="value">{html.escape(format_time(snapshot.get('next_candidate_check_at')))}</div></div>
    <div class="box"><div class="label">Last Test</div><div class="value">{html.escape(str(last_candidate_check.get('status') or '-'))}<br>{html.escape(str(last_candidate_check.get('tag') or ''))}</div></div>
    <div class="box"><div class="label">Sources</div><div class="value">extra {sources.get('extra', 0)} / sub {sources.get('subscription', 0)}</div></div>
    <div class="box"><div class="label">Fallback</div><div class="value">{html.escape(str(fallback.get('tag') or '-'))}<br>{html.escape(str(fallback.get('host') or ''))}</div></div>
    <div class="box"><div class="label">Health</div><div class="value">{html.escape(str(last_health.get('status') or '-'))} {html.escape(str(last_health.get('latency') or ''))}</div></div>
    <div class="box"><div class="label">Throughput</div><div class="value">{html.escape(str(last_throughput.get('status') or '-'))} {html.escape(str(last_throughput.get('kbps') or ''))} kbps</div></div>
  </div>
  <h2>Active Path</h2>
  <div class="grid">
    <div class="box"><div class="label">Balancer</div><div class="value">{html.escape(str(active_path.get('balancer') or '-'))}<br>{html.escape(str(active_path.get('strategy') or '-'))}</div></div>
    <div class="box"><div class="label">Selected</div><div class="value">{html.escape(str(active_path.get('selected_tag') or '-'))}<br>{html.escape(endpoint_text(active_selected))}</div></div>
    <div class="box"><div class="label">Fallback</div><div class="value">{html.escape(str(active_fallback.get('tag') or '-'))}<br>{html.escape(endpoint_text(active_fallback))}</div></div>
    <div class="box"><div class="label">API Status</div><div class="value">{html.escape(str(active_path.get('status') or '-'))}<br>{html.escape(str(active_path.get('detail') or '-'))}</div></div>
  </div>
  <h2>Health Indicators</h2>
  <div class="health-summary">
    <span class="pill ok">OK {counts.get('ok', 0)}</span>
    <span class="pill warn">WARN {counts.get('warn', 0)}</span>
    <span class="pill fail">FAIL {counts.get('fail', 0)}</span>
    <span class="pill unknown">UNKNOWN {counts.get('unknown', 0)}</span>
    <span class="pill">Updated {html.escape(format_time(snapshot.get('last_health_checks_at')))}</span>
  </div>
  <div class="health-grid">{render_health_grid(health_checks)}</div>
  <h2>Geo Assets</h2>
  <p class="muted">LoyalSoldier geoip/geosite files used by Xray. Last downloaded means the last successful runtime refresh into the persistent asset folder.</p>
  {assets_table(assets)}
  <h2>Tested Live Servers</h2>
  <p class="muted">Servers with a successful per-candidate check, sorted by fallback score.</p>
  {candidate_table(tested_live, "No tested live servers yet")}
  <h2>Logs</h2>
  <pre>{html.escape(log_lines)}</pre>
  <h2>All Candidates</h2>
  {candidate_table(all_candidates, "No candidates")}
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

    def do_GET(self):
        if self.path in ("/", "/status"):
            self.send_bytes(render_status_html(), "text/html; charset=utf-8")
            return
        if self.path == "/legacy":
            self.send_bytes(render_legacy_status_html(), "text/html; charset=utf-8")
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
    if args.status_port <= 0:
        return None
    server = ThreadingHTTPServer((args.status_listen, args.status_port), StatusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log(f"status web server listening on {args.status_listen}:{args.status_port}")
    return server
