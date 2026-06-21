# Native Xray Balancer Implementation Spec

## Goal

Reduce custom failover logic and check available VLESS servers faster without opening many concurrent subscription connections. The service must keep working as a home LAN proxy gateway and start with:

```sh
docker compose up -d --build
```

## Architecture

Use a hybrid design.

`subscription-supervisor.py` keeps responsibility for:

- loading the VLESS subscription;
- loading `vless-extra.txt`;
- filtering Russian subscription servers;
- prioritizing local extra servers;
- caching the last filtered server list in `state.json`;
- generating Xray JSON config;
- starting and restarting Xray when the generated config changes;
- Telegram notifications;
- external throughput degradation checks.

Xray takes responsibility for:

- native outbound checks via `observatory`;
- choosing a working outbound via `balancer`;
- direct routing for Russian domains and IPs;
- SOCKS, HTTP, and LAN VLESS inbounds.

## Functional Requirements

### Subscription

- Keep the subscription URL in `docker-compose.yml`.
- Refresh subscription every 2 hours: `7200` seconds.
- If the subscription is unavailable, use cached subscription candidates from `state.json`.
- Always load `vless-extra.txt`.
- Exclude Russian servers from subscription candidates.
- Do not apply the Russian server filter to `vless-extra.txt`.
- Give `vless-extra.txt` servers the highest priority.

### Xray Config Generation

- Generate multiple outbound entries instead of one active outbound.
- Use tags:
  - `proxy-extra-0`, `proxy-extra-1`, ...
  - `proxy-sub-0`, `proxy-sub-1`, ...
- Keep `direct` and `block` outbounds.
- Keep LAN inbounds:
  - SOCKS `1080`;
  - HTTP `8123`;
  - VLESS inbound `10086`.
- Keep Russian direct routing:
  - `geosite:category-ru -> direct`;
  - `geoip:ru -> direct`.

### Xray Observatory

- Enable Xray `observatory`.
- Probe URL: `https://www.gstatic.com/generate_204`.
- Checks must be sequential: `enableConcurrency: false`.
- Default probe interval: `10s`.
- Timeout target: 5-10 seconds.

### Xray Balancer

- Default non-Russian traffic goes through `balancerTag`.
- Start with strategy `leastPing`.
- The balancer selector must include all `proxy-extra-*` and `proxy-sub-*` tags.
- Use the first extra server as fallback tag when available.
- Note: `leastPing` can choose a subscription server over an extra server if it is faster. Extra priority must be modeled separately if strict priority is needed later.

### Throughput Degradation

Native Xray observatory measures latency, not real channel speed. Keep an external throughput check in the supervisor.

Proposed behavior:

- Check only the current shared proxy path, not all servers.
- Default interval: 5 minutes.
- Download size target: 1-3 MB.
- URL must be configurable.
- If speed is below threshold several times in a row, mark the connection as degraded.
- On degradation:
  - restart Xray to force balancer recalculation, or
  - exclude the suspected outbound if the selected outbound can be identified reliably, or
  - send a Telegram warning when the exact selected outbound is unknown.

### Telegram

- Send notifications only after successful recovery.
- Include:
  - reason: failed, degraded, or config refreshed;
  - previous outbound if known;
  - new or best outbound if known;
  - latency if known;
  - throughput if measured.
- In native balancer mode the exact selected outbound can be unclear because Xray may choose per new connection. The message format must handle `unknown`.

### State

`state.json` must keep:

- the last filtered candidate list;
- source: `extra` or `subscription`;
- outbound tag;
- host, port, and name;
- last latency if known;
- last throughput if known;
- last ok/fail timestamps;
- last known selected or best outbound if it can be detected.

The file stays next to `vless-extra.txt` so copying the project folder preserves state.

## Non-Functional Requirements

- Do not open mass parallel VLESS connections.
- Do not speed-test every server.
- Do not break `docker compose up -d --build`.
- Do not require manual Xray config edits.
- If the subscription is unavailable, start from extra servers or cached subscription servers.
- Validate generated config before replacing the active config when practical.

## Implementation Plan

1. Replace the current single-outbound supervisor mode with native Xray balancer mode.
2. Add multi-outbound config generation with Xray `observatory` and `balancer`.
3. Start Xray with the native balancer config.
4. Refresh subscription every 2 hours and restart Xray only when the generated config changes.
5. Keep cached subscription candidates in `state.json`.
6. Add lightweight throughput checks for the active proxy path.
7. Update Telegram notifications for native mode.
8. Verify with Python syntax check, compose validation, Docker startup, and basic SOCKS/HTTP/LAN VLESS checks.

## Risks

- Telegram "switched from A to B" can be less exact in native balancer mode.
- `leastPing` can choose subscription servers instead of extra servers.
- Observatory detects latency, not throughput.
- Large generated Xray configs need careful validation before restart.
- Some VLESS URI transport parameters, especially XHTTP extras, need runtime verification in multi-outbound mode.
