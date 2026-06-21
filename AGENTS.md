# AGENTS.md

## Purpose

This repository builds a Docker image that runs Xray-Core as a home LAN proxy gateway. The only supported runtime mode is the subscription supervisor with split DNS, native Xray balancer/observatory, hot standby failover, and a local status UI.

## Main Files

- `run.sh`: primary entrypoint; parses supervisor flags, prepares split DNS and routing rules, and starts the Python supervisor.
- `subscription-supervisor.py`: thin executable wrapper for the Python package.
- `proxy_xray/`: loads subscription candidates, generates Xray config, starts active/hot-standby Xray processes, runs health checks, and serves status UI.
- `dns-split-proxy.py`: tiny DNS relay that sends `.ru`, `.su`, and `.рф` lookups to RU DNS and other domains to global DNS.
- `Dockerfile`: builds Xray-Core and bundles runtime assets and helper scripts.
- `docker-compose.yml`: local compose example for subscription mode.

## Working Rules

- Prefer narrow edits and keep the single supported runtime mode simple.
- User-visible `run.sh` flags should be reflected in the usage output and README when relevant.
- Keep generated Xray JSON compatible with `proxy_xray/xray_config.py` and the status/test expectations.
- Treat `docker-compose.yml`, `state.json`, and `vless-extra.txt` as operator-facing artifacts. Avoid changing defaults unless the task explicitly requires it.
- Do not commit secrets or real subscription credentials. The compose file currently contains sensitive-looking values; replace with placeholders if asked to sanitize.

## Validation

Run the smallest relevant checks after edits:

- `bash -n run.sh scripts/deploy-server.sh`
- `python3 -m py_compile subscription-supervisor.py dns-split-proxy.py proxy_xray/*.py scripts/render-demo-status.py`
- `docker compose config`

If the change affects container startup or generated config, also validate with a targeted `docker build` or `docker compose up` flow when credentials and network access are available.

## Notes

- There is no formal test suite in the repo right now. Verification is mostly syntax checks plus container smoke tests.
- The working tree may contain local runtime files such as `state.json` and `__pycache__/`; do not remove or rewrite them unless the task is specifically about those artifacts.
