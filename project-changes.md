# Proxy Xray Project Changes

This file summarizes the major changes made to turn the original proxy-xray image into a home LAN proxy gateway with subscription failover, status UI, split DNS, and smoke tests.

## Current Goal

The project should start with:

```sh
docker compose up -d --build
```

After startup it should provide:

- SOCKS proxy on `1080`;
- HTTP proxy on `8123`;
- LAN VLESS inbound on `10086`;
- local status web server on `18080`;
- automatic VLESS subscription loading and refresh;
- direct routing for Russian domains and Russian IP ranges;
- split DNS for Russian and global domains;
- Telegram notification after successful recovery from a failed or degraded connection;
- a separate smoke-test container.

## Runtime Architecture

The container now supports one runtime mode only: the subscription-based home gateway. Legacy one-shot protocol generators and QR helper scripts were removed to keep the image and entrypoint focused on the deployed setup.

Removed legacy surface:

- `proxy-*.sh` one-off VLESS/VMess/Trojan config generators;
- `qrcode.sh`;
- stdin/raw JSON config mode from the entrypoint;
- old dnsmasq/proxychains/qrencode runtime dependencies;
- build-time China dnsmasq lists and unused `iran.dat`.

`run.sh` is now a thin entrypoint that:

- parses only supervisor/gateway flags;
- writes generated routing rules to a temporary JSON file;
- starts direct split DNS when enabled;
- passes the first global DNS upstream to Xray's DNS inbound;
- exits on unknown legacy flags instead of silently pretending they are supported.

The old single-file subscription supervisor was refactored into the `proxy_xray` Python package:

- `proxy_xray/main.py` parses supervisor CLI flags.
- `proxy_xray/supervisor.py` runs the main control loop.
- `proxy_xray/subscription.py` loads subscription and extra VLESS candidates.
- `proxy_xray/vless.py` parses VLESS URLs, filters/ranks candidates, and assigns tags.
- `proxy_xray/xray_config.py` generates the Xray JSON config.
- `proxy_xray/xray_process.py` starts, stops, and health-checks Xray.
- `proxy_xray/state.py` reads and writes `state.json`.
- `proxy_xray/status.py` keeps in-memory status state.
- `proxy_xray/status_server.py` serves `/`, `/json`, and `/logs`.
- `proxy_xray/telegram.py` sends recovery notifications.
- `proxy_xray/candidate_checker.py` checks one random candidate at a time.

`subscription-supervisor.py` is now a thin wrapper that imports and runs `proxy_xray.main`.

## Subscription And Candidates

The compose file runs subscription mode using a configured subscription URL. The supervisor:

- refreshes the subscription every `7200` seconds;
- loads local prioritized VLESS links from `vless-extra.txt`;
- excludes Russian subscription candidates by name/host markers;
- keeps local extra links even if they look Russian, because they are operator-controlled;
- prefers US and European subscription servers;
- selects local/US/EU candidates first for both primary and hot standby slots;
- keeps non-preferred subscription regions as fallback only when no preferred usable candidate is available;
- caches filtered subscription candidates in `state.json`;
- can start from cached candidates if the subscription URL is temporarily unavailable;
- can retry a failed startup subscription refresh through the already-running local SOCKS proxy.

Subscription fetch route:

- default mode is `auto`;
- startup tries the subscription URL directly because Xray is not running yet;
- if startup direct fetch fails, the supervisor starts from `vless-extra.txt` or cached state when possible;
- after Xray starts, a quick retry runs after `15` seconds through `socks5h://127.0.0.1:1080`;
- normal refresh remains every `7200` seconds.

Candidate tags are generated as:

- `proxy-extra-0`, `proxy-extra-1`, ...
- `proxy-sub-0`, `proxy-sub-1`, ...

## Native Xray Observatory And Balancer

The generated Xray config uses native Xray balancer/observatory inside each runtime slot:

- `observatory` checks `proxy-extra-*` and `proxy-sub-*` outbounds;
- `enableConcurrency` is `false`, so Xray does not probe all VLESS servers concurrently;
- default probe interval is `10s`;
- balancer tag is `auto`;
- default strategy is `leastPing`;
- default catch-all traffic goes through the balancer;
- fallback tag is the first candidate after ranking.

The supervisor now prepares two small pools instead of single-candidate slots:

- active pool, `3` candidates by default;
- hot standby pool, `3` candidates by default.
- active and hot standby pools each reserve up to one live candidate from `vless-extra.txt` when available; the rest of the pool is filled from subscription candidates. If only one live extra URI exists, hot standby may reuse it instead of spending another subscription slot.

The active pool receives public traffic through the stable TCP switches. The standby pool runs in a second Xray process and remains ready for slot-level failover.

Startup now performs an active-slot preflight check before attaching public ports. A dead pool head is soft-quarantined and another active pool is tried. During runtime, a healthy hot standby can be promoted after the first full active-path failure, so the system does not wait for several public timeout cycles when a ready standby path already exists.

Candidates with a recent successful per-candidate check are sorted first. This makes the native fallback prefer a known live server after Xray restart/failover while observatory probes catch up.

Fallback order is now score-based instead of a simple "last OK first" sort. The score keeps local extra servers preferred by default, but a recently working subscription server can outrank a recently failed extra server. The score also penalizes recent failures, high latency, stale successful checks, and non-preferred subscription regions while keeping smaller bonuses for preferred regions, transport type, and measured throughput. The generated Xray config still receives one ordered candidate list; the score is only used to decide that order.

## Active Path Health And Degradation Checks

The supervisor still checks the active shared proxy path through SOCKS `1080`.

Health checks:

- default URL: `https://www.gstatic.com/generate_204`;
- compose interval: `20` seconds;
- failed checks increase the failure counter;
- slow checks above `--sub-degrade-latency` increase the degradation counter.

Quality download checks:

- default URL: Cloudflare speed endpoint with a 512 KB download;
- compose interval: `60` seconds;
- default minimum speed: `1000` kbps;
- repeated slow quality checks trigger hot-standby failover.

Throughput checks:

- default URL: Cloudflare speed endpoint with a 2 MB download;
- default interval: `300` seconds;
- default minimum speed: `1500` kbps;
- heavy throughput is kept as a quality metric by default in compose.

When failure or degradation reaches configured limits, the supervisor promotes the hot standby slot when available.

## Random Candidate Checker

The project now has a sequential random candidate checker.

Behavior:

- checks one random VLESS candidate at a time;
- uses a temporary local Xray process and temporary local SOCKS inbound;
- never opens mass parallel VLESS checks;
- waits with jitter between checks;
- default jitter is between `120` and `300` seconds;
- writes successful checks to `state.json`;
- updates the web status `Tested Live Servers` list.

CLI flags:

```sh
--candidate-check-min-interval 120
--candidate-check-max-interval 300
--candidate-check-timeout 10
```

The status page shows:

- `Next Test`;
- `Last Test`;
- `Tested Live Servers`;
- fallback score and score reasons for each candidate;
- full `All Candidates` list at the bottom.

## LAN VLESS Inbound

The container exposes a stable plain VLESS inbound for local devices:

- listen address: `0.0.0.0`;
- port: `10086`;
- protocol: VLESS over TCP;
- security: `none`;
- UUID is configured in `docker-compose.yml`.

This is intended only for the home LAN. Android TV boxes, phones, or computers can connect through V2RayTun or compatible clients. The service then sends outbound traffic through the auto-selected Xray balancer.

A QR code was generated as `vless-lan-qr.png`.

## Direct Routing For Russian Sites

The compose file includes direct routing rules:

```sh
--domain-direct geosite:category-ru
--ip-direct geoip:ru
```

This means:

- Russian geosite domains go direct;
- Russian GeoIP destinations go direct;
- other traffic goes to the Xray balancer unless another rule overrides it.

The smoke test checks that these direct rules are present in the generated status JSON.

## Split DNS

The project now has `dns-split-proxy.py`, a small dependency-free DNS relay.

In split mode:

- `.ru`, `.su`, and `.рф` domains go to Russian DNS;
- all other domains go to global DNS;
- RU and global upstreams can be comma-separated fallback lists;
- the relay tries the last working upstream first and falls back to the next upstream when one is unavailable;
- upstream queries use TCP/53;
- DNS requests are made directly from the container, not through Xray;
- `/etc/resolv.conf` inside the container points to `127.0.0.1`.

Compose uses:

```sh
--dns-global 8.8.8.8,1.1.1.1
--dns-ru 77.88.8.8,77.88.8.1
--dns-split-ru
```

The TCP upstream behavior was added because UDP DNS from Docker was observed to time out in this environment.

## GeoIP And Geosite Assets

LoyalSoldier `geoip.dat` and `geosite.dat` are no longer only build-time files inside the image. At startup the supervisor prepares a persistent asset directory:

```sh
/opt/proxy-xray/assets
```

Compose mounts it as `./assets`. Behavior:

- bundled assets from the image seed the directory if files are missing;
- `geoip.dat` and `geosite.dat` are refreshed from LoyalSoldier at startup;
- refresh is repeated every `86400` seconds;
- `assets-state.json` stores last successful download time, downloaded size, and last error;
- if a scheduled refresh replaces files, Xray is restarted so new assets are used;
- if GitHub is unavailable, startup continues with the existing local assets.

The status page shows the local asset file time, size, last runtime download time, and last update error.

## Telegram Notifications

Telegram notification support was added for successful recovery after failure or degradation.

Important behavior:

- no notification is sent for the initial startup selection;
- notification is attempted only after Xray was restarted and health check succeeded;
- messages include recovery reason, fallback/candidate information, latency, and throughput when available;
- notifications are sent through the active SOCKS proxy, so if recovered connectivity is not actually usable the Telegram request can fail without stopping the service.

Secrets are configured through compose/environment and should not be copied into documentation.

## Status Web Server

The built-in status server listens on `18080`.

Endpoints:

- `/` shows HTML status;
- `/json` returns status JSON;
- `/diagnostics` runs live direct/SOCKS/HTTP URL probes and DNS probes;
- `/diagnostics.json` returns the same sanitized diagnostics in machine-readable form;
- `/diagnostics/bundle` downloads the same sanitized diagnostic JSON for sharing;
- `/logs` returns recent supervisor logs.

The HTML page currently shows:

- Xray state;
- total candidate count;
- tested live count;
- display timezone from `TZ`;
- last successful subscription update time;
- last subscription fetch route;
- next random candidate test time;
- last random candidate test result;
- source counts;
- active path from Xray's local balancer API when available;
- current selected outbound, active pool, and hot standby pool;
- split health indicators for Xray, SOCKS, HTTP, LAN VLESS, throughput, direct internet, subscription, RU DNS, global DNS, and Telegram API;
- tested live servers;
- logs;
- all candidates at the bottom.

Diagnostic URLs are configured with repeated `--diagnostic-url` flags. Compose includes `generate_204`, Cloudflare 512 KB, and `https://pikabu.ru/`. Diagnostic output is redacted for VLESS URIs, subscription URLs, Telegram-looking tokens, and UUIDs.

## Test Container

`docker-compose.test.yml` defines `proxy-client-test`, a separate container using the same built image.

Run:

```sh
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm proxy-client-test
```

The smoke test checks:

- status server JSON;
- SOCKS proxy on `1080`;
- HTTP proxy on `8123`;
- LAN VLESS inbound on `10086`;
- throughput through SOCKS, HTTP, and LAN VLESS;
- split DNS port and domain classification;
- RU/global DNS resolution;
- Russian direct routing config;
- random candidate checker configuration;
- `geoip.dat` and `geosite.dat` assets from LoyalSoldier;
- status logs endpoint.

## Operator Files

These files are intentionally operator-facing:

- `docker-compose.yml`;
- `vless-extra.txt`;
- `state.json`;
- `assets/`;
- `vless-lan-qr.png`.

`state.json` lives next to `vless-extra.txt` so the whole folder can be copied to another machine while preserving cached subscription candidates and measured state.

`state.json` now uses `schema_version: 2`. Each candidate keeps a bounded recent-check history plus rolling quality stats: success rate, failure streak, latency EWMA, and throughput EWMA. These history signals feed fallback score reasons so stable servers can outrank one-off successes. If the file is corrupted, startup moves it to `state.json.corrupt.<timestamp>` and continues with an empty state instead of crashing.

## Deployment

The project includes a local one-command deploy script for updating the home server over SSH:

```sh
DEPLOY_HOST=192.168.1.10 \
DEPLOY_USER=user \
DEPLOY_PATH=/home/user/proxy-xray \
scripts/deploy-server.sh
```

Current behavior:

- rsyncs the project to the server;
- copies local `.env` and `vless-extra.txt`;
- preserves server-side `state.json` and `assets/` by default;
- creates missing remote runtime files/directories;
- run `docker compose config --quiet`;
- builds the `proxy-xray` image;
- recreates the service;
- checks the local status endpoint on the server.

This avoids exposing the home server to the public internet and avoids GitHub Actions or inbound SSH from the internet.

## Failover Decision State

- Failover trigger selection moved into `proxy_xray/failover.py` and is covered by unit tests.
- `/json` and the status UI expose `failover_state` with state, kind, reason, cooldown, counters, and standby readiness.
- Runtime slot switching still lives in `supervisor.py`; this keeps behavior stable while making the decision layer testable.

## Known Limitations

- Xray balancer information is read through the local RoutingService API for both active and standby slots.
- `/json` exposes `active_observatory` and `standby_observatory` snapshots with API status, selected outbound, fallback, pool, and raw `xray api bi` output.
- When a slot health or quality check succeeds, the supervisor records the Xray selected outbound in candidate history and gives it a small recent-selection score bonus. The current Xray API output does not expose per-outbound latency, so selection alone is not treated as a successful health check.
- `Tested Live Servers` grows gradually because only one random candidate is tested per jitter interval.
- Xray observatory latency data is not currently exposed by `xray api bi`; selected outbound data is normalized into candidate history only after successful slot checks.
- Throughput checks measure the active shared proxy path, not every candidate.
- Direct-routing behavior is verified by generated config and smoke access, not by packet capture.
