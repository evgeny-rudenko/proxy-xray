#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DEPLOY_HOST="${DEPLOY_HOST:-}"
DEPLOY_USER="${DEPLOY_USER:-}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_PATH="${DEPLOY_PATH:-proxy-xray}"
DEPLOY_SERVICE="${DEPLOY_SERVICE:-proxy-xray}"
DEPLOY_BUILD="${DEPLOY_BUILD:-1}"
DEPLOY_SMOKE="${DEPLOY_SMOKE:-1}"
DEPLOY_COPY_STATE="${DEPLOY_COPY_STATE:-0}"
DEPLOY_COPY_ASSETS="${DEPLOY_COPY_ASSETS:-0}"
DEPLOY_SSH_OPTS="${DEPLOY_SSH_OPTS:-}"

usage() {
    cat <<EOF
Usage:
  DEPLOY_HOST=192.168.1.10 [DEPLOY_USER=user] [DEPLOY_PATH=/home/user/proxy-xray] scripts/deploy-server.sh

Options via env:
  DEPLOY_HOST        Required server hostname or IP.
  DEPLOY_USER        Optional SSH user.
  DEPLOY_PORT        SSH port, default: 22.
  DEPLOY_PATH        Remote project path, default: proxy-xray in the SSH user's home.
  DEPLOY_SERVICE     Compose service name, default: proxy-xray.
  DEPLOY_BUILD       Build image before restart, default: 1.
  DEPLOY_SMOKE       Run local status smoke check on server, default: 1.
  DEPLOY_COPY_STATE  Copy local state.json to server, default: 0.
  DEPLOY_COPY_ASSETS Copy local assets/ to server, default: 0.
  DEPLOY_SSH_OPTS    Extra ssh options, for example: "-i ~/.ssh/home-server".
EOF
}

if [ -z "${DEPLOY_HOST}" ]; then
    usage >&2
    exit 2
fi

if [[ "${DEPLOY_PATH}" == *"'"* ]]; then
    echo "DEPLOY_PATH must not contain single quotes: ${DEPLOY_PATH}" >&2
    exit 2
fi

SSH_TARGET="${DEPLOY_HOST}"
if [ -n "${DEPLOY_USER}" ]; then
    SSH_TARGET="${DEPLOY_USER}@${DEPLOY_HOST}"
fi

SSH_BASE=(ssh -p "${DEPLOY_PORT}")
if [ -n "${DEPLOY_SSH_OPTS}" ]; then
    # shellcheck disable=SC2206
    EXTRA_SSH_OPTS=(${DEPLOY_SSH_OPTS})
    SSH_BASE+=("${EXTRA_SSH_OPTS[@]}")
fi

RSYNC_SSH="ssh -p ${DEPLOY_PORT}"
if [ -n "${DEPLOY_SSH_OPTS}" ]; then
    RSYNC_SSH="${RSYNC_SSH} ${DEPLOY_SSH_OPTS}"
fi

EXCLUDES=(
    --exclude ".git/"
    --exclude ".DS_Store"
    --exclude "__pycache__/"
    --exclude "*.pyc"
    --exclude "vless-lan-qr.png"
    --exclude "assets/*.download"
)

if [ "${DEPLOY_COPY_STATE}" != "1" ]; then
    EXCLUDES+=(--exclude "state.json")
fi

if [ "${DEPLOY_COPY_ASSETS}" != "1" ]; then
    EXCLUDES+=(--exclude "assets/")
fi

echo "==> Preparing ${SSH_TARGET}:${DEPLOY_PATH}"
"${SSH_BASE[@]}" "${SSH_TARGET}" "mkdir -p '${DEPLOY_PATH}' '${DEPLOY_PATH}/assets' && { [ -f '${DEPLOY_PATH}/state.json' ] || printf '{}\n' > '${DEPLOY_PATH}/state.json'; }"

echo "==> Syncing project files"
rsync -az --delete -e "${RSYNC_SSH}" "${EXCLUDES[@]}" "${ROOT_DIR}/" "${SSH_TARGET}:${DEPLOY_PATH}/"

REMOTE_COMMANDS=(
    "set -euo pipefail"
    "cd '${DEPLOY_PATH}'"
    "docker compose config >/dev/null"
)

if [ "${DEPLOY_BUILD}" = "1" ]; then
    REMOTE_COMMANDS+=("docker compose build '${DEPLOY_SERVICE}'")
fi

REMOTE_COMMANDS+=(
    "docker compose up -d --force-recreate '${DEPLOY_SERVICE}'"
    "docker ps --filter name=proxy-xray --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
)

if [ "${DEPLOY_SMOKE}" = "1" ]; then
    REMOTE_COMMANDS+=(
        "for i in \$(seq 1 30); do curl -fsS http://127.0.0.1:18080/json >/dev/null 2>&1 && exit 0; sleep 2; done; echo 'status smoke check failed' >&2; exit 1"
    )
fi

echo "==> Building and restarting on server"
printf "%s\n" "${REMOTE_COMMANDS[@]}" | "${SSH_BASE[@]}" "${SSH_TARGET}" bash -s

echo "==> Deploy finished"
