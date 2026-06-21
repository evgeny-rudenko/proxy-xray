# AGENTS.md

## Purpose

This repository builds a Docker image that runs Xray-Core as a client proxy with configuration generated from CLI flags. The main logic lives in shell entrypoints plus one Python supervisor for subscription-based failover.

## Main Files

- `run.sh`: primary entrypoint; parses CLI flags, assembles JSON config, and starts Xray.
- `subscription-supervisor.py`: loads subscription candidates, selects a working VLESS server, runs health checks, and performs failover.
- `proxy-*.sh`: protocol-specific config generators. Keep behavior aligned with the naming convention already used here.
- `qrcode.sh`: reconstructs the connection URI from the generated config and prints a QR code.
- `Dockerfile`: builds Xray-Core and bundles runtime assets and helper scripts.
- `docker-compose.yml`: local compose example for subscription mode.

## Working Rules

- Prefer narrow edits. This repo is mostly shell scripts with repeated patterns; match the existing style before introducing abstractions.
- Preserve CLI compatibility in `run.sh`. New flags should follow the current `getopt` and `case` structure and should be reflected in the usage output and README when user-visible.
- Keep generated Xray JSON compatible with current helpers. Changes in one `proxy-*.sh` script often need corresponding handling in `qrcode.sh` or `run.sh`.
- Treat `docker-compose.yml`, `state.json`, and `vless-extra.txt` as operator-facing artifacts. Avoid changing defaults unless the task explicitly requires it.
- Do not commit secrets or real subscription credentials. The compose file currently contains sensitive-looking values; replace with placeholders if asked to sanitize.

## Validation

Run the smallest relevant checks after edits:

- `bash -n run.sh qrcode.sh proxy-*.sh`
- `python3 -m py_compile subscription-supervisor.py`
- `docker compose config`

If the change affects container startup or generated config, also validate with a targeted `docker build` or `docker compose up` flow when credentials and network access are available.

## Notes

- There is no formal test suite in the repo right now. Verification is mostly syntax checks plus container smoke tests.
- The working tree may contain local runtime files such as `state.json` and `__pycache__/`; do not remove or rewrite them unless the task is specifically about those artifacts.
