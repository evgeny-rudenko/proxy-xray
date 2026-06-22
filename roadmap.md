# Proxy Xray Roadmap

This is a rough plan for future improvements based on the current project concept: a home LAN Xray gateway with subscription failover, direct Russian routing, split DNS, status UI, and simple Docker Compose operation.

## 1. Read Xray Observatory Results

Xray already checks outbound health internally through `observatory`, but the supervisor does not currently read those results.

Goal:

- expose real Xray outbound health in the status page;
- show which outbounds Xray considers alive or degraded;
- reduce reliance on custom inferred status.

Possible implementation:

- enable Xray API/stats service;
- read observatory results from Xray;
- add the results to `/json`;
- render them in the web UI.

Benefits:

- more accurate live-server status;
- less duplicated checking logic;
- easier debugging when balancer behavior is unclear.

Risks:

- Xray API config needs careful validation;
- exact observatory output format needs runtime verification.

## 2. Deploy Profiles And Rollback

The basic SSH deploy script already exists. The next step is to make deployment safer and easier to repeat across local/server profiles.

Goal:

```sh
./deploy.sh home
./deploy.sh home --rollback 2026-06-21_18-00
```

Expected behavior:

- read target settings from `.deploy.env` or named profile files;
- create a backup on the server before replacing files;
- preserve `state.json`, `vless-extra.txt`, `.env`, and `assets/` by default;
- optionally run the smoke-test container after deploy;
- print final status URL and proxy ports;
- support rollback to the previous uploaded version.

This keeps the current LAN-only deployment model, but reduces manual recovery work when an update is bad.

## 3. Backup And Restore Operator Files

Before deploy or major changes, save operator-facing files:

```text
backups/
  2026-06-06_15-30/
    state.json
    vless-extra.txt
    docker-compose.yml
```

Useful commands:

- create backup;
- list backups;
- restore backup;
- compare current files with backup.

The main goal is to avoid accidentally losing local servers or measured state.

## 4. Status Page Actions

Because the status page is LAN-only, add a few simple action buttons.

Candidate actions:

- refresh subscription now;
- run one random candidate check now;
- restart Xray;
- clear failed marks;
- export `state.json`;
- maybe export current generated Xray config.

Keep this minimal. The status page should remain operational, not become a full admin panel.

## 5. Structured Logs And Filters

Current logs are flat text.

Improve logging by adding categories:

- health;
- throughput;
- subscription;
- candidate checks;
- Telegram;
- DNS;
- Xray restart/config.

The web UI can show recent important events and allow filtering. `/logs` can remain a plain text endpoint.

## 6. Diagnostics Endpoint

Add a dedicated diagnostics endpoint:

```text
/diagnostics
```

It should run or display checks for:

- `.ru` DNS through RU DNS;
- global DNS through global DNS;
- `geosite:category-ru` direct rule present;
- `geoip:ru` direct rule present;
- `geoip.dat` and `geosite.dat` exist;
- asset sizes and timestamps;
- status server health;
- active proxy path health.

This can share logic with the smoke-test container where possible.

## 7. Production Layout Documentation

Document the expected server layout:

```text
/opt/proxy-xray/
  docker-compose.yml
  vless-extra.txt
  state.json
  logs/
  backups/
```

Add an `OPERATIONS.md` with common tasks:

- first install;
- deploy update;
- rollback;
- check logs;
- run smoke tests;
- view status;
- reset state;
- add local VLESS links.

## 8. Server Quality History

Store more than the last known server state.

Useful metrics:

- daily and weekly OK/FAIL counts;
- average latency;
- average throughput;
- last degradation time;
- how often the server was selected as active;
- successful switch count for this server.

This should help distinguish a server that randomly recovered from one that is consistently stable.

## 9. Bad Server Quarantine

If a server repeatedly fails, degrades, or reports very poor latency/throughput, temporarily move it into quarantine.

Possible behavior:

- quarantine for N hours after repeated fail/degraded results;
- expose quarantine reason in `/json` and the UI;
- add a manual clear-quarantine action;
- allow extra servers to be checked more often while protecting subscription servers from excessive connections.

Benefits:

- fewer useless repeated checks;
- lower chance of switching to a formally alive but bad server;
- less load on subscriptions with connection limits.

## 10. Failover Aggressiveness Profiles

Add a simple setting for automatic failover behavior:

- `conservative`: fewer switches, tolerate short drops;
- `balanced`: current behavior;
- `aggressive`: switch faster on degradation.

This is useful because TVs, browsers, and background workloads do not need the same behavior. Sometimes stability matters more; sometimes reaction speed matters more.

## 11. Manual Server Pin

Allow temporarily pinning the current server.

Options:

- until restart;
- for 1 hour;
- until the server breaks;
- until manually unpinned.

This is useful when a good server is found and the operator does not want automation to move away from it too eagerly.

## 12. Active Server Selection Reason

Show why the current active server was selected.

Example reasons:

- highest score;
- fallback after degradation;
- extra priority;
- last known good;
- selected by Xray balancer;
- subscription servers unavailable;
- pinned manually.

This should make confusing failover decisions much easier to debug.

## 13. One-Click Diagnostic Bundle

Add a diagnostic bundle export.

Contents:

- current `/json`;
- recent logs;
- generated Xray config with secrets removed;
- `geoip.dat`, `geosite.dat`, and `assets-state.json` state;
- `docker compose config`;
- live servers;
- all candidates;
- DNS split and RU direct routing state.

This is useful before server migration and for cases where the connection "works but feels slow".

## 14. Secret Sanitization

Mask secrets in exports, diagnostics, and logs.

Mask:

- Telegram token;
- subscription URL;
- UUID values in VLESS links;
- private VLESS links;
- query parameters that may contain keys.

Goal: diagnostic bundles and log snippets should be shareable without leaking access.

## 15. Lightweight Configuration Audit

Add an endpoint or command that validates operator configuration.

Check:

- subscription URL is configured;
- `vless-extra.txt` is readable;
- `state.json` is writable;
- assets directory is writable;
- DNS split is enabled;
- RU direct rules are present;
- Telegram chat/token are configured when notifications are enabled;
- exposed ports match documentation;
- status server is reachable.

## 16. Graceful Startup Profile

Make container startup more predictable and fast.

Desired order:

- seed assets from the image;
- load cached candidates from `state.json`;
- start Xray with last known good;
- refresh subscription asynchronously;
- refresh assets asynchronously;
- run candidate checks asynchronously.

Main goal: after `docker compose up`, internet should come back quickly from cache even if external resources are slow.

## 17. Minimal Local API For UI Actions

If status page actions are added, implement them through a small local API.

Possible endpoints:

- `POST /actions/subscription-refresh`;
- `POST /actions/check-random`;
- `POST /actions/restart-xray`;
- `POST /actions/clear-failed`;
- `GET /export/state`;
- `GET /export/diagnostics`.

Because the page is LAN-only, authentication is not required, but mutating actions should use `POST` and the UI should show the operation result.

## 18. TLS Fingerprint And ClientHello Fragment Experiments

Add experimental controls for anti-DPI behavior around TLS handshakes.

Ideas:

- preserve subscription-provided `fp` by default;
- allow controlled fingerprint override for extra servers and hot-standby slots;
- support stable profiles such as `chrome`, `firefox`, `safari`, instead of changing every connection;
- investigate Xray `fragment` behavior for `tlshello` and whether it applies cleanly to our VLESS TLS/REALITY outbound path;
- run A/B mode where one hot-standby slot keeps normal handshakes and another slot uses fragment/fingerprint changes.

Constraints:

- keep this disabled by default;
- prefer extra/local servers for first tests;
- show fingerprint and fragment mode in `/json` and the status UI;
- measure whether it improves availability or only adds latency.

Risk: fingerprint randomization and ClientHello fragmentation can help against some DPI setups, but can also make traffic look less natural or break some routes. Treat it as an experiment, not as a default reliability feature.

## Suggested Implementation Order

1. Graceful startup profile.
2. Server quality history.
3. Bad server quarantine.
4. Active server selection reason.
5. TLS fingerprint and ClientHello fragment experiments.
6. One-click diagnostic bundle.
7. Minimal local API for UI actions.
8. Add deploy profiles and rollback.
9. Add backup/restore for operator files.
10. Read Xray observatory results through Xray API.
11. Write `OPERATIONS.md`.
