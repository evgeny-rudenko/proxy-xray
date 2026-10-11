# Amsterdam Chain: Validation

Branch: `codex/amsterdam-chain`. Date: 2026-10-09.

## Verified

- 70 unit tests cover route priorities, two-cycle failure decisions, guarded recovery, role separation, distinct relay hosts, pinned pair attribution, bootstrap failure without node penalty, single-transaction atomic state writes, corrupted JSON, subscription outage at startup, API failure, removed subscriptions, exact relay selectors and repeated recheck requests. A successful pinned candidate probe cannot reset failures measured on the working balanced route.
- Eight isolated integration scenarios use the actual pinned Xray 26.6.1 with synthetic VLESS servers and no external network. HTTP, SOCKS and LAN VLESS work in all three modes. Direct-path, transport, relay and complete-exit failures were reproduced. Foreign traffic and the forced DNS entry remain closed when no path works; RU traffic stays direct. The relay loop cannot recurse into an exit.
- Actual direct Amsterdam and chains through two independently healthy subscription relays returned the same IPv4 exit. The report deliberately does not contain exit addresses or credentials.
- A subsequent live run found one healthy Internet relay whose Amsterdam pair failed. Per-exit relay selectors now include only proven compatible pairs; an isolated regression verifies that an incompatible but otherwise healthy relay can still serve as emergency exit without being selected for Amsterdam delivery. Earlier successful live runs are evidence of feasibility, not a guarantee that every relay always reaches Amsterdam.
- The local Compose image was built and started. Public ports are only TCP 1080, 8123, 10086 and 18080. The abandoned local `proxy-xray-extra` container was removed; private inputs, old state and assets were preserved.
- Local HTTP/SOCKS requests and dashboard DNS policy passed smoke checks.
- A two-minute local status observation recorded seven healthy direct-Amsterdam samples, no failed checks, no unreadable status and no stopped core. Two live route-recheck requests were coalesced into one command while the proxies remained usable.
- Killing the working local Xray process recovered connectivity in 11.7 seconds, with one working core after recovery.
- Playwright checked 1440px desktop and 390px mobile layouts: no horizontal overflow, no clipped tested controls, no browser errors. Incremental updates do not reload the page.
- Syntax, Compose and exact-value secret scans of current operator credentials against source and Git history passed. Private runtime files remain ignored.

## Not Yet Accepted

- Several-hour soak testing, real-world automatic failover under ISP instability, sustained session behavior and traffic-budget measurement remain pending. Short smoke/fault checks are not a substitute for this.
- Synthetic tests exercise the three-mode public protocols; the live Internet check verifies direct and chained egress, not a real data-center outage.
- No upstream push, main-branch modification or server-side Amsterdam configuration change was performed. Home deployment was authorized separately after the local implementation checks; its results follow below.

## Home Server Deployment (2026-10-09)

- Deployed the current working tree of `codex/amsterdam-chain` into `/home/evgeny/proxy-xray-amsterdam`, with Compose project/container `proxy-xray-amsterdam` and image `proxy-xray:amsterdam`. A server-only `docker-compose.override.yml` isolates its image and container names from the old project.
- Built the image on the x86_64 server before stopping the old container. The server image passed all 70 unit tests and all eight isolated integration scenarios without an external test network.
- Kept `/home/evgeny/proxy-xray`, its stopped `proxy-xray` container and its original image. The original image also has the backup tag `proxy-xray:pre-amsterdam-20261009-124603`. Private inputs were copied from the server's existing installation, not replaced with laptop credentials. Assets and cached state were copied into the new directory independently.
- Read startup logs: split DNS starts with global requests through SOCKS and `direct-fallback=False`; foreign routing opens only after an end-to-end check. One direct Amsterdam transport passed while three other initial transport checks failed. Later checks caused a temporary subscription exit and then a guarded return to direct Amsterdam. These are actual network observations, not a claim that every transport is healthy.
- HTTP 8123 and SOCKS 1080 both returned HTTP 204 from the primary control site and HTTP 200 from the independent backup site. The Web UI returned HTTP 200. A real request through the public LAN VLESS 10086 entry also passed, using the existing UUID internally without publishing it.
- The client smoke script passed after startup pool preparation. The initial attempt ran during startup/failover and did not pass; it must not be counted as a successful readiness check. The new container had zero Docker restarts when inspected, and only TCP 1080, 8123, 10086 and 18080 were published.
- A later direct-path failure switched to `chained_amsterdam`; pinned delivery through two subscription relays had passed. The final status check in that mode showed all health checks green and zero Docker restarts, and the HTTP/SOCKS smoke script passed again. Several extra transport checks still failed, so this is evidence of a working verified chain, not universal transport availability.
- Native API selection can temporarily be empty while a verified fallback serves traffic. The current text parser can mistake the numeric row marker in that response for a selected tag; this display-only edge case still needs a regression/fix. No extra code change was included in this deployment.
- Several-hour observation remains pending; intermittent health failures were seen during the short startup observation. A successful smoke check is not evidence of sustained stability.

Manage the new installation from `/home/evgeny/proxy-xray-amsterdam` with the explicit Compose project name `proxy-xray-amsterdam`. `DEPLOYMENT.md` and a private `deployment-receipt.json` in that server directory retain the deployment details.

For immediate rollback, run these two commands in order on the home server. Do not remove either project or prune its images:

```sh
docker stop proxy-xray-amsterdam
docker start proxy-xray
```

## Direct Return Stabilization (2026-10-09)

- Added a 300-second continuous balanced-route health window for voluntary return to direct Amsterdam. Failed checks or a gap over 2.5 check intervals reset this proof. Pinned transport checks can authorize startup/failure diagnostics but cannot accumulate recovery stability.
- A direct route that fails within 180 seconds after activation delays the next voluntary return for 600 seconds, increasing to a maximum of 900 seconds after another rapid failure. Sustained direct operation clears the penalty. Startup and a genuinely failed current route bypass this wait; the foreign route is still closed until verified.
- The final server-built image passed 81 unit tests, including restart of the stability window after an isolated error, repeated rapid failures, bounded retry delays, missing probes, pinned-proof isolation and emergency bypass. All eight isolated actual-Xray integration scenarios passed again. The local Docker engine was not used.
- Before replacement, the previous Amsterdam image and source were saved as `proxy-xray:amsterdam-before-return-guard-20261009-133152` and `/home/evgeny/proxy-xray-amsterdam/rollback-return-guard-20261009-133152`. The original stopped `proxy-xray` installation remains separate and untouched.
- Status/UI publish accumulated direct stability and retry time, while route logs now distinguish a failed active route from a voluntary preference return. Both README files describe the defaults and the emergency exception. Timers are process-local and reset on supervisor restart.
- The first activation showed a real rapid direct failure starting the 600-second retry pause; subscription egress remained usable and a verified Amsterdam chain subsequently became active. A final timer-reset edge correction was separately covered by the final 81-test run and rebuilt before final rollout. Longer observation remains necessary; these startup results do not prove several-hour stability.
- On the final image, HTTP/SOCKS/UI smoke checks and a real LAN VLESS request passed. A separate three-minute observation produced nine readable status samples, no stopped cores, two emergency-subscription samples and seven chained-Amsterdam samples; there was no return to direct Amsterdam during that observation. The final status check showed `chained_amsterdam`, green checks and zero Docker restarts. The 600/900-second penalty progression is covered by unit tests; no rapid direct failure occurred during this final three-minute window. The anonymous report is in `runtime/chain/return-guard-observation.json` on the server.

## Routing Policy Switch (2026-10-09)

- Added persistent `auto` and `subscription_only` operator policies, separate from the actual route. The latter allows only Amsterdam through subscription or emergency subscription egress; it never permits Direct as a recovery path. The same allowed-route list governs selection, staged recovery, startup jobs and manual/candidate probes.
- The final server-built image `sha256:091f1194390a820f570fc5d9de7537a9f8196442feef5ad0b82053bb631a914e` passed 101 unit tests and nine isolated actual-Xray scenarios with `--network none`. New cases cover persistence failure, corrupted state, pending-command coalescing, Origin validation, forbidden probes/overrides, failed API closure and staged fallback. The new integration scenario verifies HTTP/SOCKS/LAN VLESS and forced DNS through chain and emergency subscription, then foreign blocking while RU stays direct, even with a healthy Direct path still available.
- Playwright checked the switch at 1440px desktop and 390px mobile using synthetic responses and an installed Chrome executable: no overflow, browser errors or full-page reloads; handlers still work after fragment replacement. Screenshot artifacts are temporary and contain no operator data.
- Updated only `/home/evgeny/proxy-xray-amsterdam`. Before update, its previous image and sources were saved as `proxy-xray:amsterdam-before-return-guard-20261009-183239` and `/home/evgeny/proxy-xray-amsterdam/rollback-return-guard-20261009-183239`. The original stopped project was not changed.
- A live POST selected `subscription_only`; the command completed and the private state stored that value. Recreating the experimental container preserved the choice, reverified its allowed route, and started emergency subscription egress. Reading startup logs showed zero Direct supervisor probes, zero core preparation errors and zero core-crash restarts. The state file stayed private; validation read only its policy field inside the container without changing file permissions.
- HTTP 8123 and SOCKS 1080 returned HTTP 204 from the primary canary and HTTP 200 from the independent backup. Web UI and a real LAN VLESS request passed. Global/RU DNS health checks were green. Seven samples over a 70-second observation stayed on the selected policy with no forbidden Direct activation and a running core. The live chain was not the active path during this short window; chain/fallback behavior is covered by the isolated scenario, not asserted as a live outage experiment.
- One initial validation helper could not read the root-owned private state from the host and timed out; this was a validation-tool permission issue, not a gateway failure. The corrected container-side read confirmed persistence, including after recreation. No several-hour stability claim is made.
- After validation, the switch was returned to `auto` through its POST endpoint, without restarting Xray. The command completed and the stored policy matched. The actual route remained the working emergency subscription exit; changing operator policy does not bypass recovery guards or force an unverified Amsterdam route. No main-branch changes or upstream push were made.
- The subsequent final status snapshot showed `auto` with `chained_amsterdam`, a running core and all health indicators green: the verified chain returned through the ordinary recovery mechanism after the switch was restored.

## Reproduce Checks

```sh
python3 -m unittest discover -s tests
python3 scripts/check-private-values.py
docker compose config --quiet
docker run --rm --network none --entrypoint python3 \
  -v "$PWD/proxy_xray:/proxy_xray:ro" -v "$PWD/tests:/tests:ro" \
  proxy-xray:local /tests/chain-integration.py
bash tests/client-smoke.sh
docker compose exec -T proxy-xray python3 - < scripts/check-chain-live.py
```

`tests/core-restart-check.py` intentionally kills the working Xray core. Run it only inside an authorized local test container, not on an operational server.
Playwright checks are in `tests/ui-chain-check.cjs`; `CHROMIUM_PATH` can select an already installed Chrome executable.

For a three-hour local observation without additional provider traffic:
```sh
python3 scripts/observe-chain.py --seconds 10800 --output runtime/chain/observation.json
```
The report contains only route names, health states and counts, not connection credentials or endpoint addresses. This command is provided for the pending soak stage; a several-hour run was not completed in this implementation turn.
