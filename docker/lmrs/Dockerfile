ARG VLLM_BASE=vllm/vllm-openai:latest
FROM ${VLLM_BASE}

ARG LMRS_UID=42424
ARG LMRS_GID=42424

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LMRS_CONFIG=/etc/lmrs/config.json \
    LMRS_LOG_DIR=/var/log/lmrs \
    HF_HOME=/var/lmrs/hf-cache \
    HUGGINGFACE_HUB_CACHE=/var/lmrs/hf-cache/hub \
    TRANSFORMERS_CACHE=/var/lmrs/hf-cache/transformers \
    XDG_CACHE_HOME=/var/lmrs/cache \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

RUN set -eux; \
    groupadd --system --gid "${LMRS_GID}" lmrsgrp; \
    useradd --system --uid "${LMRS_UID}" --gid lmrsgrp --home-dir /var/lmrs --shell /usr/sbin/nologin lmrsuser; \
    mkdir -p /etc/lmrs/certs /var/lmrs/cache /var/lmrs/hf-cache /var/log/lmrs; \
    chown -R lmrsuser:lmrsgrp /var/lmrs /var/log/lmrs; \
    chgrp -R lmrsgrp /etc/lmrs; \
    chmod 0750 /etc/lmrs /etc/lmrs/certs /var/lmrs /var/log/lmrs

RUN python3 -m pip install --upgrade pip \
    && python3 -m pip install "lmcache"

WORKDIR /opt/lmrs
COPY pyproject.toml README.md ./
COPY lmrs ./lmrs
COPY tools ./tools
RUN python3 -m pip install --ignore-installed "fastapi[standard]==0.136.3" "typer>=0.20,<0.26" ".[server]" \
    && python3 - <<"PYVERIFY"
from importlib.metadata import version
from packaging.version import Version
fastapi_version = Version(version("fastapi"))
typer_version = Version(version("typer"))
assert Version("0.133.0") <= fastapi_version < Version("0.137.0"), fastapi_version
assert Version("0.20.0") <= typer_version < Version("0.26.0"), typer_version
PYVERIFY
RUN chown -R lmrsuser:lmrsgrp /opt/lmrs && chmod -R u=rwX,g=rX,o= /opt/lmrs

COPY docker/lmrs/docker-entrypoint.sh /usr/local/bin/lmrs-entrypoint
RUN chmod 0755 /usr/local/bin/lmrs-entrypoint

USER lmrsuser:lmrsgrp
EXPOSE 8012
ENTRYPOINT ["/usr/local/bin/lmrs-entrypoint"]
