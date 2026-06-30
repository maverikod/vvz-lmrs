#!/usr/bin/env bash
#
# LMRS container entrypoint: starts the internal vLLM model server (loopback
# only) and the LMRS mcp_proxy_adapter server, and supervises both — if either
# exits, the other is stopped and the container exits so Docker can restart it.
#
# Configuration comes from environment (see /etc/default/lmrs) and the mounted
# /etc/lmrs/config.json. Only LMRS is published externally; vLLM binds loopback.
#
# Author: Vasiliy Zdanovskiy
# email: vasilyvz@gmail.com
set -euo pipefail

LMRS_CONFIG="${LMRS_CONFIG:-/etc/lmrs/config.json}"
LMRS_RUN_DIR="${LMRS_RUN_DIR:-/var/lmrs}"
LMRS_LOG_DIR="${LMRS_LOG_DIR:-/var/log/lmrs}"
VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${VLLM_PORT:-8000}"
LMRS_MODEL="${LMRS_MODEL:-}"
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"

mkdir -p "${LMRS_RUN_DIR}" "${LMRS_LOG_DIR}"

if [ ! -f "${LMRS_CONFIG}" ]; then
    echo "lmrs-entrypoint: FATAL: config not found at ${LMRS_CONFIG} (mount /etc/lmrs)" >&2
    exit 1
fi

VLLM_PID=""
LMRS_PID=""

terminate() {
    [ -n "${VLLM_PID}" ] && kill "${VLLM_PID}" 2>/dev/null || true
    [ -n "${LMRS_PID}" ] && kill "${LMRS_PID}" 2>/dev/null || true
}
trap terminate TERM INT

# vLLM model server — loopback only, never exposed outside the container.
if [ -n "${LMRS_MODEL}" ]; then
    echo "lmrs-entrypoint: starting vLLM model=${LMRS_MODEL} on ${VLLM_HOST}:${VLLM_PORT}"
    # shellcheck disable=SC2086
    vllm serve "${LMRS_MODEL}" --host "${VLLM_HOST}" --port "${VLLM_PORT}" ${VLLM_EXTRA_ARGS} &
    VLLM_PID=$!
else
    echo "lmrs-entrypoint: LMRS_MODEL not set; starting LMRS without an in-container vLLM"
fi

# LMRS adapter server — the only externally published service.
echo "lmrs-entrypoint: starting LMRS adapter with config ${LMRS_CONFIG}"
python3 -m lmrs --config "${LMRS_CONFIG}" &
LMRS_PID=$!

# Exit as soon as either supervised process exits; stop the survivor first.
wait -n
terminate
wait
