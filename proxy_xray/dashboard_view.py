from .view_helpers import *

def dashboard_v5_fragments(snapshot=None):
    snapshot = snapshot or status_snapshot()
    current = (snapshot.get("exit") or {}).get("transport") or {}
    relay = (snapshot.get("relay") or {}).get("transport") or {}
    transition = snapshot.get("route_transition") or {}
    required = transition.get("required_successes", 3)
    direct_guard = transition.get("direct_recovery") or {}
    policy = snapshot.get("route_policy", "auto")
    policy_command = snapshot.get("route_policy_command") or {}
    command_state = policy_command.get("status", "done")
    policy_pending = command_state in ("queued", "running")
    policy_value = (policy_command.get("payload") or {}).get("policy", policy) if policy_pending else policy
    policy_note = {"queued": "Ожидает применения", "running": "Применяется", "failed": "Ошибка применения",
                   "done": "Режим применён"}.get(command_state, "Режим применён")
    return_wait = transition.get("hold_remaining", 0)
    if policy == "auto" and snapshot.get("route_mode") in ("chained_amsterdam", "subscription_exit"):
        return_wait = max(return_wait, direct_guard.get("retry_remaining", 0), direct_guard.get("stable_remaining", 0))
    mode = snapshot.get("route_mode") or "unavailable"
    names = {"direct_amsterdam": "Direct Amsterdam", "chained_amsterdam": "Subscription → Amsterdam",
             "subscription_exit": "Subscription emergency exit", "unavailable": "No foreign route"}
    checks = snapshot.get("health_checks") or {}
    counts = health_counts(checks)
    kind = "fail" if mode == "unavailable" or counts.get("fail") else "warn" if mode == "subscription_exit" or counts.get("warn") else "ok"
    title = "Gateway has failures" if kind == "fail" else "Gateway needs attention" if kind == "warn" else "Gateway is healthy"
    note = "Exit point changed: using subscription" if mode == "subscription_exit" else names.get(mode, "UNKNOWN")
    sources = snapshot.get("sources") or {}
    live = snapshot.get("tested_live_candidates") or []
    exits = snapshot.get("exit_pool") or []
    relays = snapshot.get("relay_pool") or []
    speed = snapshot.get("last_throughput") or {}
    assets = snapshot.get("assets") or {}
    dns = snapshot.get("dns_transport") or {}
    fetch = snapshot.get("subscription_fetch") or {}
    escape = lambda value: html.escape(str(value if value is not None else "UNKNOWN"))
    guard_details = (f'<small>Direct stable {direct_guard.get("stable_seconds", 0)} / {direct_guard.get("required_stable_seconds", 300)}s · retry {direct_guard.get("retry_remaining", 0)}s</small>'
                     if policy == "auto" else "")
    if policy != "auto" and mode != "subscription_exit":
        return_wait = 0
    rows = []
    for key, item in (snapshot.get("route_checks") or {}).items():
        rows.append(f'<div><span>{escape(names.get(key, key))}</span><strong>{"OK" if item.get("ok") else "FAIL"}</strong><small>{escape(short_time(item.get("time")))} · {escape(format_metric(item.get("latency"), "s"))}</small></div>')
    return {
        "v5-subline": f"{escape(timezone_label())} · refreshed {escape(format_time(snapshot.get('last_health_checks_at')))} · live update 15s",
        "v5-quota": subscription_quota_badge(snapshot.get("subscription_usage") or {}),
        "v5-overview": f'''
          <div class="v5-state {kind}"><span class="v5-state-dot"></span><div><h2>{title}</h2><p>{escape(note)}</p></div></div>
          <div class="v5-kpi-row">
            <div class="v5-kpi"><span>Candidates</span><strong>{snapshot.get("candidates_count", 0)}</strong><small>exit {sources.get("extra", 0)} / relay {sources.get("subscription", 0)}</small></div>
            <div class="v5-kpi"><span>Tested live</span><strong>{len(live)}</strong><small>independent transport checks</small></div>
            <div class="v5-kpi"><span>Throughput</span><strong>{escape(throughput_text(speed.get("kbps")))}</strong><small>selected route</small></div>
            <div class="v5-kpi"><span>Next test</span><strong>{escape(short_time(snapshot.get("next_candidate_check_at")))}</strong><small>sequential · jitter</small></div>
          </div>''',
        "v5-current": f'''
          <div class="v5-panel-title"><span>Exit and delivery path</span><a href="/json">JSON</a></div>
          <div class="v5-policy" data-command-status="{escape(command_state)}">
            <div class="v5-policy-options" role="group" aria-label="Режим маршрутизации">
              <button type="button" data-route-policy="auto" aria-pressed="{str(policy_value == 'auto').lower()}">Обычный</button>
              <button type="button" data-route-policy="subscription_only" aria-pressed="{str(policy_value == 'subscription_only').lower()}">Subscription → Amsterdam</button>
            </div>
            <small class="v5-policy-feedback" role="status">{escape(policy_note)}{' · ' + escape(policy_command.get('detail')) if command_state == 'failed' else ''}</small>
          </div>
          <div class="v5-current-name">{escape((snapshot.get("exit") or {}).get("location"))}</div>
          <div class="v5-current-endpoint">{escape(endpoint_text(current) if current else "Transport UNKNOWN")}</div>
          <div class="v5-chip-row"><span class="v5-chip {kind}">{escape(names.get(mode, "UNKNOWN"))}</span>
          <span class="v5-chip {'ok' if snapshot.get('xray_running') else 'fail'}">Xray {'running' if snapshot.get('xray_running') else 'stopped'}</span></div>
          <div class="v5-current-grid">
            <div><span>Exit transport</span><strong>{escape(current.get("tag"))}</strong><small>{escape(current.get("network"))} / {escape(current.get("security"))}</small></div>
            <div><span>Relay</span><strong>{escape(relay.get("tag") if mode != "direct_amsterdam" else "Direct")}</strong><small>{escape(endpoint_text(relay) if relay else "No subscription traffic" if mode == "direct_amsterdam" else "UNKNOWN")}</small></div>
            <div><span>Score / latency</span><strong>{escape(score_value(current))}</strong><small>{escape(format_metric(current.get("last_latency"), "s"))} · {escape(score_reasons(current, limit=3))}</small></div>
            <div><span>Return guard</span><strong>{return_wait}s</strong><small>{escape(transition.get("reason"))}</small>{guard_details}<small>Chain {transition.get("return_successes", {}).get("chained_amsterdam", 0)} / {required}</small></div>
          </div><div class="v5-current-endpoint">Switched {escape(short_time(transition.get("time")))}</div>''',
        "v5-health": f'<div class="v5-panel-title"><span>Health indicators</span><small>OK {counts.get("ok", 0)} · WARN {counts.get("warn", 0)} · FAIL {counts.get("fail", 0)}</small></div><div class="v5-health-list">{compact_health_rows(checks)}</div>',
        "v5-pools": f'''<div class="v5-panel-title"><span>Exit and relay pools</span><small>Amsterdam {len(exits)} · relay {len(relays)}</small></div>
          <div class="v5-pool-grid"><div><h3>Amsterdam transports</h3>{compact_server_cards(exits, "No exit transports")}</div>
          <div><h3>Subscription relays</h3>{compact_server_cards(relays, "No verified relays yet")}</div></div>''',
        "v5-servers": f'<div class="v5-panel-title"><span>Live transports by score</span><a href="/servers/live">open full list</a></div><div class="v5-server-list">{compact_server_cards(live[:7], "No tested live transports yet")}</div>',
        "v5-routing": f'''<div class="v5-panel-title"><span>Routing and assets</span><a href="/diagnostics">Diagnostics</a></div>
          <div class="v5-routing-grid"><div><span>Geo assets</span><strong>{escape((assets.get("last_status") or {}).get("status", "ready"))}</strong><small>last {escape(short_time(assets.get("last_success_at")))}</small></div>
          <div><span>Subscription</span><strong>{escape(short_time(snapshot.get("last_subscription_success_at")))}</strong><small>{escape(fetch.get("last_method"))} / {escape(fetch.get("mode"))}</small></div>
          <div><span>Global DNS</span><strong>Selected route</strong><small>last {escape(dns.get("last_route"))} / {escape(dns.get("last_status"))}</small></div>
          {''.join(rows)}</div>''',
        "v5-events": f'<div class="v5-panel-title"><span>Event timeline</span><a href="/logs">plain logs</a></div><div class="v5-events">{event_timeline_rows(snapshot.get("logs") or [])}</div>',
    }


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
    .v5-title-line {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
    .v5-quota {{ width: 260px; border: 1px solid #c6dcf6; border-radius: 8px; background: var(--blue-soft); padding: 6px 9px; }}
    .v5-quota > div:first-child {{ display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }}
    .v5-quota span {{ color: var(--blue); font-size: 10px; text-transform: uppercase; font-weight: 850; }}
    .v5-quota strong {{ font-size: 13px; line-height: 1.1; }}
    .v5-quota small {{ display: block; margin-top: 2px; font-size: 10px; line-height: 1.2; }}
    .v5-quota-track {{ height: 4px; margin-top: 5px; border-radius: 999px; overflow: hidden; background: #c8dcf5; }}
    .v5-quota-track i {{ display: block; height: 100%; border-radius: inherit; background: #258ae0; }}
    .v5-subline, .v5-muted, small {{ color: var(--muted); }}
    .v5-subline {{ margin-top: 4px; font-size: 13px; }}
    .v5-actions {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
    .v5-action-form {{ margin: 0; }}
    .v5-policy {{ margin: 12px 0; }}
    .v5-policy-options {{ display: inline-flex; flex-wrap: wrap; gap: 2px; padding: 3px; background: var(--panel-soft); border: 1px solid var(--line); border-radius: 6px; max-width: 100%; }}
    .v5-policy-options button {{ border: 0; border-radius: 4px; padding: 8px 10px; font: inherit; font-size: 12px; font-weight: 700; color: var(--muted); background: transparent; cursor: pointer; overflow-wrap: anywhere; }}
    .v5-policy-options button[aria-pressed="true"] {{ color: var(--blue); background: var(--blue-soft); }}
    .v5-policy-options button:focus-visible {{ outline: 2px solid var(--blue); }}
    .v5-policy-feedback {{ display: block; margin-top: 5px; color: var(--muted); font-size: 11px; }}
    .v5-policy[data-command-status="failed"] .v5-policy-feedback {{ color: var(--red); }}
    .v5-button {{ min-height: 34px; border: 1px solid var(--line-strong); background: rgba(255,255,255,.82); color: #273545; border-radius: 8px; padding: 7px 11px; font-size: 13px; font-weight: 750; text-decoration: none; display: inline-grid; place-items: center; }}
    button.v5-button {{ cursor: pointer; font-family: inherit; }}
    .v5-button.primary {{ color: var(--blue); background: var(--blue-soft); border-color: #c6dcf6; }}
    .v5-button.warn {{ color: var(--amber); background: var(--amber-soft); border-color: #efd08f; }}
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
        <div class="v5-title-line"><h1>proxy-xray status</h1><div data-fragment="v5-quota">{fragments["v5-quota"]}</div></div>
        <div class="v5-subline" data-fragment="v5-subline">{fragments["v5-subline"]}</div>
      </div>
    </div>
    <nav class="v5-actions">
      <a class="v5-button" href="/servers/live">Live</a>
      <a class="v5-button" href="/diagnostics">Diagnostics</a>
      <a class="v5-button" href="/client">QR</a>
      <form class="v5-action-form" method="post" action="/control/recheck-route">
        <button class="v5-button warn" type="submit" title="Run a sequential route recheck">Перепроверить маршрут</button>
      </form>
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
  let policyRequest = false;
  let pendingPolicy = null;
  const keys = [
    "v5-subline",
    "v5-quota",
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
          if (key === 'v5-current' && policyRequest) continue;
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
  document.addEventListener('click', async (event) => {{
    const button = event.target.closest('[data-route-policy]');
    if (!button) return;
    pendingPolicy = button.dataset.routePolicy;
    const panel = button.closest('.v5-policy');
    panel.dataset.commandStatus = 'queued';
    panel.querySelectorAll('[data-route-policy]').forEach(item => {{
      item.setAttribute('aria-pressed', String(item === button));
    }});
    panel.querySelector('.v5-policy-feedback').textContent = 'Отправляется';
    if (policyRequest) return;
    policyRequest = true;
    try {{
      while (pendingPolicy !== null) {{
        const policy = pendingPolicy;
        pendingPolicy = null;
        const response = await fetch('/control/route-policy', {{
          method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ policy }}),
        }});
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
      }}
    }} catch (error) {{
      pendingPolicy = null;
      panel.dataset.commandStatus = 'failed';
      panel.querySelector('.v5-policy-feedback').textContent = 'Не удалось отправить выбор. Повторите попытку.';
      return;
    }} finally {{
      policyRequest = false;
    }}
    await refreshFragments();
  }});
  const form = document.querySelector('.v5-action-form');
  form.addEventListener('submit', async (event) => {{
    event.preventDefault();
    const button = form.querySelector('button');
    button.disabled = true;
    try {{
      const response = await fetch(form.action, {{ method: 'POST' }});
      if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
      await refreshFragments();
    }} catch (error) {{
      console.warn('route recheck failed', error);
    }} finally {{
      button.disabled = false;
    }}
  }});
  document.addEventListener("visibilitychange", () => {{
    if (!document.hidden) refreshFragments();
  }});
}})();
</script>
</body>
</html>"""
    return body.encode("utf-8")
