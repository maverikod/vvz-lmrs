#!/bin/bash
set -euo pipefail

if [ "$(id -un)" != "lmrsuser" ] || [ "$(id -gn)" != "lmrsgrp" ]; then
  echo "LMRS container must run as lmrsuser:lmrsgrp" >&2
  exit 64
fi

: "${LMRS_CONFIG:=/etc/lmrs/config.json}"
: "${LMRS_LOG_DIR:=/var/log/lmrs}"
: "${LMRS_MODEL:=}"
: "${VLLM_HOST:=127.0.0.1}"
: "${VLLM_PORT:=8000}"
: "${VLLM_EXTRA_ARGS:=}"
: "${LMCACHE_CONFIG_FILE:=/etc/lmrs/lmcache.yaml}"
: "${NVIDIA_VISIBLE_DEVICES:=all}"
: "${NVIDIA_DRIVER_CAPABILITIES:=compute,utility}"

mkdir -p /var/lmrs/cache /var/lmrs/hf-cache "$LMRS_LOG_DIR"

if [ ! -r "$LMRS_CONFIG" ]; then
  echo "LMRS config is not readable: $LMRS_CONFIG" >&2
  exit 66
fi

pids=()
shutdown() {
  for pid in "${pids[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait || true
}
trap shutdown TERM INT EXIT

if [ -n "$LMRS_MODEL" ]; then
  lmcache_args=()
  # -f, not -r: the host run script bind-mounts this path, so when the file is
  # absent on the host docker creates a DIRECTORY here. A directory is readable,
  # so -r silently enabled the KV connector with a directory as its config.
  if [ -f "$LMCACHE_CONFIG_FILE" ]; then
    export LMCACHE_CONFIG_FILE
    lmcache_args=(--kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}')
  fi

  # vLLM stays inside the LMRS container and receives GPU resources from docker run --gpus.
  vllm serve "$LMRS_MODEL" \
    --host "$VLLM_HOST" \
    --port "$VLLM_PORT" \
    "${lmcache_args[@]}" \
    ${VLLM_EXTRA_ARGS} &
  pids+=("$!")
fi

python3 -m lmrs --config "$LMRS_CONFIG" &
pids+=("$!")

wait -n "${pids[@]}"
exit $?
