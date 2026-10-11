from .view_helpers import *

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
    active = data.get("exit") or {}
    standby = data.get("relay") or {}
    failover = summary.get("route_transition") or {}
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
    <div class="box"><div class="label">Route</div><div class="value">{html.escape(str(summary.get('route_mode') or 'UNKNOWN'))}<br>{html.escape(str(failover.get('reason') or '-'))}</div></div>
    <div class="box"><div class="label">Exit</div><div class="value">{html.escape(str(active.get('location') or 'UNKNOWN'))}<br>{html.escape(str(active.get('selected_tag') or 'UNKNOWN'))}</div></div>
    <div class="box"><div class="label">Relay</div><div class="value">{html.escape(str(standby.get('selected_tag') or 'UNKNOWN'))}</div></div>
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
