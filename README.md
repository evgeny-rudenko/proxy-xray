# proxy-xray

[![Telegram](https://img.shields.io/badge/Telegram-@endominion-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/endominion)

[Русская версия](README.ru.md)

## Amsterdam Exit Architecture

This experimental branch uses a private Amsterdam server as the preferred exit:
1. Direct Amsterdam.
2. Subscription relay → Amsterdam when the direct path fails.
3. Subscription exit when Amsterdam cannot be reached through the tested paths.
4. Block foreign traffic if no route works; keep RU routing direct.

The private exit transports come only from `vless-extra.txt`. Subscription nodes are first-hop relays or emergency exits, never mixed into the Amsterdam transport balancers. All extra transports are included; the relay pool contains at most three independently verified nodes and prefers different hosts.

Xray uses separate `leastPing` balancers and a `dialerProxy`/loopback chain. Route changes use Routing API, without restarting the working core. Only pool/configuration changes need a staged replacement. The old active/hot mixed pools and Extra Pool override are removed.

## Setup

```sh
cp .env.example .env
cp vless-extra.example.txt vless-extra.txt
```

Set `XRAY_SUB_URL` to your provider's subscription URL. Put one private Amsterdam VLESS link per line in `vless-extra.txt`; keep its TLS/Reality/gRPC/XHTTP parameters unchanged.

Generate `INBOUND_VLESS_ID` once:
```sh
python3 -c 'import uuid; print(uuid.uuid4())'
```
This UUID is your gateway's LAN client credential, not an upstream server ID. Use the same UUID in clients. The LAN inbound has no TLS and must not be exposed to the internet.

Optional Telegram:
- Create a bot using **@BotFather** and place its HTTP API token in `TELEGRAM_BOT_TOKEN`.
- Send the bot a message. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` privately and use `message.chat.id` as `TELEGRAM_CHAT_ID`. Do not publish the URL or response.
- `TZ=Europe/Moscow` controls displayed times.

```sh
docker compose config --quiet
docker compose up -d --build
```

## Ports And Clients

| TCP port | Service |
| --- | --- |
| 1080 | SOCKS5 |
| 8123 | HTTP proxy, including HTTPS CONNECT |
| 10086 | LAN VLESS |
| 18080 | Dashboard, diagnostics, logs and client QR |

Set an HTTP client to `http://<gateway-LAN-IP>:8123`. Firefox SOCKS5 clients should enable **Proxy DNS when using SOCKS v5**.
Open `http://<gateway-LAN-IP>:18080/client`, or click **QR**, to obtain the LAN VLESS link and QR. Using localhost creates a link unsuitable for other devices.

Only these ports are published. Core API, probe ports and the DNS transport entrypoint bind to loopback. Public SOCKS UDP forwarding is not provided by the TCP front switch.

## Recovery And DNS

Selected-path checks run every 20 seconds, with two independent health sites. Two consecutive failed cycles trigger a verified alternate route. Returning from emergency subscription egress to the Amsterdam chain requires three successful checks spaced by 20 seconds and a 180-second hold. Returning to direct Amsterdam additionally requires five minutes of uninterrupted balanced-route health checks; a failed check or a gap over 2.5 configured check intervals (50 seconds by default) resets that window. Individual pinned transport checks cannot authorize this return.

Before leaving an active Direct route after two failed cycles, the supervisor sequentially tries up to two alternative gRPC/XHTTP exit transports on the working core. An alternative must pass two end-to-end checks. A successful transport is retained through Routing API, including staged core replacements, without changing the exit or restarting Xray. If no alternative passes, normal chain/subscription failover applies. This rescue does not run in subscription-only policy; the choice of transport is reverified after a process restart.

Failed health requests log the primary/backup site, HTTP status, curl exit code, elapsed time and sanitized error. Balanced-route diagnostics record API-selected transport before and after the request; a selection change is not proof of which transport carried every request. `/json` includes these details in `last_health.attempts` and transport snapshots, and reports the latest successful rescue in `last_direct_rescue`. Pinned candidate histories retain request details too. The native observatory still uses the primary site independently of the supervisor's two-site check.

If direct Amsterdam fails within three minutes of activation, the next voluntary return is delayed by ten minutes, increasing to fifteen minutes after another rapid failure. Five minutes of stable operation on the active direct path resets this penalty. In normal policy, a real failure of the current route bypasses all return waits: a recently verified direct path can still rescue connectivity. Startup opens the first permitted verified route without waiting five minutes. The dashboard shows direct-path stability and retry countdowns; a healthy Amsterdam chain is not replaced merely because a brief direct probe succeeded.

Pinned checks distinguish a broken relay from a broken Amsterdam path. Each exit transport can use only relays with a verified pair: healthy Internet egress does not prove access to Amsterdam. Per-exit balancers reuse the same three relay outbounds without additional subscription copies. Up to two independent relay hosts and two suitable exit transports are tested before reporting Amsterdam unreachable through tested routes. That is not proof of a data-centre outage. Candidate checks are sequential with a random 120–300 second interval. Speed measurements are warnings, not immediate failover triggers.

Split DNS is used by the container itself:
- RU suffixes resolve through direct RU DNS; `geosite:category-ru` and `geoip:ru` route directly.
- Foreign DNS-over-TCP uses an internal SOCKS entrypoint forced through the selected foreign route, before RU rules. There is no direct foreign-DNS fallback.
- Endpoint/subscription bootstrap is independent of the selected exit. Amsterdam can also be resolved through a verified relay; TLS names are preserved.
- Already cached DNS answers may remain available; new unresolved foreign queries fail when no foreign route exists.

A failed core restarts with capped exponential backoff (1–30 seconds) and route verification. Existing TCP sessions cannot migrate between exits and may disconnect. A failed Amsterdam host affects all its transports. A chain adds latency and charges all chained traffic to the subscription. Startup checks and native observatory also consume traffic.

## Dashboard And State

The dashboard shows **exit and delivery path**, native selected transports or UNKNOWN, relay/exit pools, route check times, transition reasons, return guard, speed and subscription quota. Emergency subscription exit is yellow because the exit point changes.

**Перепроверить маршрут** queues a sequential recheck. Repeated clicks are coalesced. It never locks an unverified route or bypasses return hysteresis. Blocks update without full page reload.

The **Exit and delivery path** panel has a routing-policy switch:

- **Обычный** (normal): direct Amsterdam, then subscription → Amsterdam, then emergency subscription exit.
- **Subscription → Amsterdam**: prefer the chain, with emergency subscription exit if the chain fails. Direct Amsterdam is never selected, even after restart or core/configuration recovery. Supervisor direct-Amsterdam checks are disabled; relay checks remain sequential. Native Xray observatory may still probe configured direct transports; these probes do not authorize user traffic through Direct.

The choice is saved atomically in private state and survives restart. A failed save leaves the previous choice unchanged. Missing/corrupt state defaults to normal, with foreign traffic blocked until a route is verified. If all permitted routes fail, foreign traffic and uncached global DNS fail closed; RU stays direct. The UI separates requested policy, application status and actual route; subscription emergency exit stays yellow. Switching policy does not restart Xray or migrate existing TCP sessions: reconnect clients to stop using an old path. Returning to normal keeps the existing Direct recovery guards.

`POST /control/route-policy` accepts JSON `{"policy":"auto"}` or `{"policy":"subscription_only"}` and returns a queued command ID. Commands run sequentially; the latest queued choice wins. Foreign-Origin requests are rejected and CORS is not enabled. `/json` includes `route_policy` and `route_policy_command` separately from actual `route_mode`. Web UI remains LAN-only and unauthenticated; do not expose it to the internet.

Endpoints: `/`, `/json`, `/logs`, `/diagnostics`, `/diagnostics.json`, `/diagnostics/bundle`, `/servers/live`, `/servers/all`, `/client`. Diagnostics read saved checks instead of starting parallel network probes. The classic dashboard is removed.

State is private: `runtime/chain/state.json` contains cached subscription credentials and separate bounded exit/relay/pair histories. It is atomically replaced and synced; malformed files are moved aside. Saved successes never authorize a route after restart without new verification. The old root `state.json` is not modified. Keep `.env`, `vless-extra.txt`, `state.json`, `runtime/` and generated QR artifacts out of Git and backups intended for sharing.

## Settings And Tests

`docker compose run --rm proxy-xray --help` lists the supported entrypoint flags. Defaults: `--relay-pool-size 3`, `--check-interval 20`, `--max-failures 2`, `--return-successes 3`, `--return-hold 180`, `--direct-return-stable 300`, `--direct-retry-delay 600`, `--direct-retry-max 900`, `--direct-failure-window 180`, `--candidate-check-min-interval 120`, `--candidate-check-max-interval 300`, `--refresh-interval 7200`. The direct-return timers are process-local and reset on supervisor restart; every startup still rechecks permitted routes. A cached/extra-only startup retries subscription refresh after 30 seconds. Removed mixed-pool flags and `DNS_GLOBAL_ROUTE` are not supported.

```sh
python3 -m unittest discover -s tests
bash -n run.sh scripts/deploy-server.sh tests/client-smoke.sh
docker compose config --quiet
docker run --rm --network none --entrypoint python3 \
  -v "$PWD/proxy_xray:/proxy_xray:ro" -v "$PWD/tests:/tests:ro" \
  proxy-xray:local /tests/chain-integration.py
docker compose logs --tail 100
```

The isolated integration tests use synthetic VLESS servers and no internet access. Only their fixtures allow loopback destinations; production restrictions remain unchanged. An optional live check compares the same exit IPv4 via two actual relays without printing addresses or credentials:
```sh
docker compose exec -T proxy-xray python3 - < scripts/check-chain-live.py
```

This branch is being tested locally. No automatic home-server deployment is performed. Several-hour operational observation remains a separate acceptance step.

## Credits

Originally based on the samuelhbne/proxy-xray Docker wrapper; extended with supervision, split DNS, diagnostics, UI and Amsterdam chaining.
