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
: "${LMRS_VRAM_RESERVE_MIB:=}"
: "${LMCACHE_CONFIG_FILE:=/etc/lmrs/lmcache.yaml}"
: "${NVIDIA_VISIBLE_DEVICES:=all}"
: "${NVIDIA_DRIVER_CAPABILITIES:=compute,utility}"

mkdir -p /var/lmrs/cache /var/lmrs/hf-cache "$LMRS_LOG_DIR"

# The adapter talks to vLLM over this URL. Deriving it here keeps one setting
# pair: changing VLLM_PORT moved the server but left the adapter calling 8000.
vllm_client_host="$VLLM_HOST"
if [ "$vllm_client_host" = "0.0.0.0" ]; then
  vllm_client_host=127.0.0.1
fi
export VLLM_BASE_URL="${VLLM_BASE_URL:-http://${vllm_client_host}:${VLLM_PORT}}"

# LMCache is enabled by the same condition that enables the KV connector below,
# so the status command reports what the runtime actually runs with.
if [ -f "$LMCACHE_CONFIG_FILE" ]; then
  export LMRS_LMCACHE_ENABLED=1
else
  export LMRS_LMCACHE_ENABLED=0
fi

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

  # vLLM reads --gpu-memory-utilization as a fraction of TOTAL device memory and
  # refuses to start when less than that is free, so a value pinned in
  # /etc/default/lmrs fails as soon as another process holds VRAM and cannot be
  # right on the local card and a rented one at once. Derive it from this card
  # unless the operator pinned it deliberately.
  utilization_args=()
  case " $VLLM_EXTRA_ARGS " in
    *" --gpu-memory-utilization"*) ;;
    *)
      reserve_args=()
      if [ -n "$LMRS_VRAM_RESERVE_MIB" ]; then
        reserve_args=(--reserve-mib "$LMRS_VRAM_RESERVE_MIB")
      fi
      if utilization=$(python3 -m lmrs.vram "${reserve_args[@]}" 2>&1); then
        utilization_args=(--gpu-memory-utilization "$utilization")
        echo "LMRS: derived --gpu-memory-utilization $utilization from free VRAM" >&2
      else
        echo "LMRS: cannot derive --gpu-memory-utilization: $utilization" >&2
        exit 69
      fi
      ;;
  esac

  # vLLM stays inside the LMRS container and receives GPU resources from docker run --gpus.
  vllm serve "$LMRS_MODEL" \
    --host "$VLLM_HOST" \
    --port "$VLLM_PORT" \
    "${lmcache_args[@]}" \
    "${utilization_args[@]}" \
    ${VLLM_EXTRA_ARGS} &
  pids+=("$!")
fi

python3 -m lmrs --config "$LMRS_CONFIG" &
pids+=("$!")

wait -n "${pids[@]}"
exit $?
