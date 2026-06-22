# Server deploy

`scripts/deploy-server.sh` updates the project on a home server over SSH, rebuilds the Docker image, and recreates the `proxy-xray` container.

## Requirements

- SSH access from this machine to the server.
- `docker compose` installed on the server.
- `rsync` installed locally and on the server.

## First deploy

Choose a folder on the server, then run from the local project folder:

```shell
DEPLOY_HOST=192.168.1.10 \
DEPLOY_USER=user \
DEPLOY_PATH=/home/user/proxy-xray \
scripts/deploy-server.sh
```

The script creates the remote folder and an empty `state.json` if it does not exist.

By default the script copies local `.env` and `vless-extra.txt`, but does not copy local `state.json`, `assets/`, or `vless-lan-qr.png`, so server runtime state is preserved between updates.

## Regular update

```shell
DEPLOY_HOST=192.168.1.10 \
DEPLOY_USER=user \
DEPLOY_PATH=/home/user/proxy-xray \
scripts/deploy-server.sh
```

What happens:

1. Project files are synced to the server with `rsync --delete`.
2. Local `.env` and `vless-extra.txt` are copied.
3. Remote `state.json` and `assets/` are kept.
4. `docker compose config` is checked.
5. `docker compose build proxy-xray` is run.
6. `docker compose up -d --force-recreate proxy-xray` is run.
7. `http://127.0.0.1:18080/json` is checked locally on the server.

## Useful options

Use a non-standard SSH port:

```shell
DEPLOY_HOST=192.168.1.10 DEPLOY_PORT=2222 scripts/deploy-server.sh
```

Use a specific SSH key:

```shell
DEPLOY_HOST=192.168.1.10 DEPLOY_SSH_OPTS="-i ~/.ssh/home-server" scripts/deploy-server.sh
```

Skip image build and only recreate from the existing image:

```shell
DEPLOY_HOST=192.168.1.10 DEPLOY_BUILD=0 scripts/deploy-server.sh
```

Copy local runtime state intentionally:

```shell
DEPLOY_HOST=192.168.1.10 DEPLOY_COPY_STATE=1 scripts/deploy-server.sh
```

Copy local geo assets intentionally:

```shell
DEPLOY_HOST=192.168.1.10 DEPLOY_COPY_ASSETS=1 scripts/deploy-server.sh
```
