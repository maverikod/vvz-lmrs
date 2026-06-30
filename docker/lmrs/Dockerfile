# syntax=docker/dockerfile:1
#
# LMRS image: vLLM (+ LMCache) model server bundled with the LMRS
# mcp_proxy_adapter server. Only the LMRS server is exposed; vLLM listens on
# loopback inside the container and is reachable only by LMRS.
#
# Author: Vasiliy Zdanovskiy
# email: vasilyvz@gmail.com

ARG VLLM_BASE=vllm/vllm-openai:latest
FROM ${VLLM_BASE}

ARG VERSION=dev
LABEL org.opencontainers.image.title="lmrs" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.description="Local Model Runtime Service (LMRS): mcp_proxy_adapter server bundled with vLLM + LMCache" \
      org.opencontainers.image.authors="Vasiliy Zdanovskiy <vasilyvz@gmail.com>"

# LMCache: KV-cache offloading/sharing layer for vLLM.
RUN pip install --no-cache-dir lmcache

# LMRS package (the [server] extra pulls mcp-proxy-adapter).
WORKDIR /opt/lmrs
COPY pyproject.toml README.md LICENSE ./
COPY lmrs ./lmrs
RUN pip install --no-cache-dir ".[server]"

# In-container defaults (overridden by /etc/default/lmrs via --env-file and by
# the mounted /etc/lmrs/config.json).
ENV LMRS_CONFIG=/etc/lmrs/config.json \
    LMRS_RUN_DIR=/var/lmrs \
    LMRS_LOG_DIR=/var/log/lmrs \
    LMRS_PORT=8012 \
    VLLM_HOST=127.0.0.1 \
    VLLM_PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY docker/lmrs/docker-entrypoint.sh /usr/local/bin/lmrs-entrypoint
RUN chmod 0755 /usr/local/bin/lmrs-entrypoint \
    && mkdir -p /etc/lmrs /var/lmrs /var/log/lmrs

# Only the LMRS adapter port is exposed; vLLM stays on loopback (not exposed).
EXPOSE 8012

ENTRYPOINT ["/usr/local/bin/lmrs-entrypoint"]
