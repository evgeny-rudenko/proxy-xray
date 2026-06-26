# Completed Proxy Xray Roadmap Items

This document keeps roadmap items that have already been implemented, so the active roadmap can stay focused on future work.

## 1. Split Status Into Clear Health Indicators

Completed: 2026-06-07.

The status page should not show a vague "connection is down" state when only one subsystem is affected.

Implemented health indicators:

- Xray process;
- SOCKS proxy;
- HTTP proxy;
- LAN VLESS inbound;
- active path throughput;
- direct internet from the container;
- subscription state;
- RU DNS;
- global DNS;
- Telegram API reachability without sending a message.

This makes cases like "YouTube through SOCKS is fast, but the page says no connection" easier to diagnose.

## 2. Better Fallback Scoring

Completed: 2026-06-07.

Implemented scoring model:

- extra servers get a base priority;
- live subscription servers can outrank failed extra servers;
- slow live servers are penalized;
- recently failed servers are penalized;
- very old successful checks gradually lose weight;
- transport type can still affect priority;
- preferred region and measured throughput add smaller bonuses;
- each candidate exposes `fallback_score` and `fallback_score_reasons` in `/json` and the status tables.

The result is one ordered candidate list that Xray uses for fallback and balancer config generation.

## 3. GeoIP/Geosite Asset Visibility And Updates

Completed: 2026-06-07.

Implemented:

- show asset size and build/download timestamp in status;
- seed assets from the image into a persistent runtime asset directory;
- refresh LoyalSoldier `geoip.dat` and `geosite.dat` at startup and then on a schedule;
- store last successful download time and last error in `assets-state.json`;
- restart Xray after successful scheduled asset replacement;
- verify persistent assets in smoke tests;
- avoid making service startup depend on GitHub availability.

The service keeps working when GitHub is unavailable at startup.

## 4. Single Runtime Mode And Legacy Cleanup

Completed: 2026-06-21.

The project is now intentionally optimized for one deployed mode: the home LAN subscription gateway.

Implemented:

- removed legacy `proxy-*.sh` one-shot protocol generators;
- removed `qrcode.sh`;
- simplified `run.sh` into a subscription-supervisor entrypoint;
- removed old stdin/raw JSON entrypoint paths;
- removed unused runtime dependencies such as dnsmasq, proxychains, and qrencode;
- removed build-time China dnsmasq lists and unused `iran.dat`;
- updated smoke tests so LAN VLESS uses the real configured UUID from `.env`;
- validated build, startup, status endpoint, SOCKS, HTTP, LAN VLESS, split DNS, RU direct routing, and geo assets.

This reduces the image and code surface to the only mode actually used on the home server.

## 5. V2 Pool Mode Without Single-Candidate Runtime/API

Completed: 2026-06-25.

After v2 stabilization, the remaining active/standby single-candidate surface was removed.

Implemented:

- active and hot standby pools now default to size `3`;
- removed the pool-selection branch that forced the current single candidate when `size=1`;
- standby pool no longer seeds from a separate single standby candidate;
- `/json` no longer publishes top-level `fallback` and `standby` fields;
- status UI uses active backend, active pool, hot standby, and observatory snapshots;
- removed the old `/legacy` status page;
- moved smoke tests to pool-based status fields.

The generated Xray config still uses native `fallbackTag`; that is Xray's first-outbound fallback inside a pool, not a separate legacy runtime mode.

## 6. Failover State Machine

Completed: 2026-06-26.

Implemented:

- decision layer moved to `proxy_xray/failover.py`;
- failover reasons are modeled as separate kinds: full connection failure, fast standby failure, latency degradation, quality degradation, and throughput degradation;
- cooldown suppression is separate from the failover reason;
- full failure bypasses cooldown, while degradation can be suppressed by cooldown;
- `failover_state` is exposed in `/json`, diagnostics, and the status UI;
- state includes kind, reason, full_failure, standby_ready, cooldown_remaining, and check counters;
- transitions are covered by unit tests in `tests/test_failover.py`;
- smoke tests verify that `failover_state` is present.

Switch/rebuild execution still lives in `supervisor.py`, but the switching decision is now a separate testable state machine.

## 7. Diagnostics And Domain Probes

Completed: 2026-06-26.

Implemented:

- `/diagnostics` shows live direct/SOCKS/HTTP probes;
- `/diagnostics.json` returns machine-readable sanitized diagnostics;
- `/diagnostics/bundle` downloads sanitized diagnostic JSON;
- diagnostic URLs are configured with repeated `--diagnostic-url` flags and support CSV values;
- default probes include `generate_204`, small download, and `pikabu.ru`;
- DNS diagnostics checks RU/global split DNS;
- output redacts VLESS URIs, subscription URLs, Telegram-looking tokens, and UUIDs;
- smoke tests verify the diagnostics endpoint and absence of secret-looking data.
