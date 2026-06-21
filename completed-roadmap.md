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
