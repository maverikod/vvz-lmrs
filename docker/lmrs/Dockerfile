# The image must be inference-complete: a model loads and runs with nothing
# installed at build-time-unknown versions. Both pins are required build
# arguments with no default, so a build cannot quietly float to a new vLLM or
# LMCache release; the guards below reject an unpinned value.
ARG VLLM_BASE
FROM ${VLLM_BASE}

ARG VLLM_BASE
ARG LMCACHE_VERSION
ARG LMRS_UID=42424
ARG LMRS_GID=42424

RUN set -eux; \
    case "${VLLM_BASE}" in \
        "") echo "VLLM_BASE must be set to an exact vLLM base image tag" >&2; exit 1 ;; \
        *:latest) echo "VLLM_BASE must be an exact tag, not ${VLLM_BASE}" >&2; exit 1 ;; \
        *:*) : ;; \
        *) echo "VLLM_BASE must carry an explicit tag, got ${VLLM_BASE}" >&2; exit 1 ;; \
    esac; \
    case "${LMCACHE_VERSION}" in \
        "") echo "LMCACHE_VERSION must be set to an exact LMCache version" >&2; exit 1 ;; \
        *) : ;; \
    esac

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
    mkdir -p /etc/lmrs/certs /var/lmrs/cache /var/lmrs/hf-cache /var/lmrs/lmcache /var/log/lmrs; \
    chown -R lmrsuser:lmrsgrp /var/lmrs /var/log/lmrs; \
    chgrp -R lmrsgrp /etc/lmrs; \
    chmod 0750 /etc/lmrs /etc/lmrs/certs /var/lmrs /var/log/lmrs

RUN python3 -m pip install --upgrade pip \
    && python3 -m pip install "lmcache==${LMCACHE_VERSION}"

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

# Deliberately NO VOLUME declarations. A VOLUME here makes docker create an
# anonymous volume for any declared path the run command does not explicitly
# bind, and that anonymous volume SHADOWS a bind mount of a parent directory:
# with `-v /var/lmrs:/var/lmrs` and `VOLUME /var/lmrs/hf-cache`, the container
# saw an empty anonymous volume instead of the host's downloaded weights, and
# every restart minted a fresh empty one, so vLLM re-downloaded the model from
# scratch on every start and never came up. Persistent state lives on explicit
# bind mounts owned by packaging/bin/lmrs-container.
ENTRYPOINT ["/usr/local/bin/lmrs-entrypoint"]
