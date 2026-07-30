#!/usr/bin/env bash
#
# Single-run release pipeline for LMRS.
#
# One invocation performs the whole release: it proves every artifact carries
# the same version, builds the Docker image and the Debian package, builds the
# server and client Python distributions, pushes the image to Docker Hub, and
# publishes the client to PyPI.
#
# Version discipline: the server version in pyproject.toml is the single source.
# The client version, the Debian changelog version and the image tag must equal
# it; a mismatch aborts before anything is built or published.
#
# Credentials: Docker Hub credentials come from .env at the project root and
# PyPI credentials from the operator's ~/.pypirc. Neither is ever echoed, and
# no step prompts interactively.
#
# The image build, the Docker Hub push and the .deb build are delegated to
# scripts/release_build.sh rather than reimplemented here: a second docker build
# invocation would be a parallel release path that could drift from the one the
# packaging already uses, including its required VLLM_BASE and LMCACHE_VERSION
# pins.
#
# Usage:
#   ./build.sh                 # full release: build, push, publish
#   ./build.sh --skip-publish  # everything except the PyPI publish
#   ./build.sh --dry-run       # verify versions and tooling, build nothing
#
# Author: Vasiliy Zdanovskiy
# email: vasilyvz@gmail.com
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { printf '%b==>%b %s\n' "${GREEN}" "${NC}" "$*"; }
warn()  { printf '%b!!%b %s\n' "${YELLOW}" "${NC}" "$*" >&2; }
fail()  { printf '%bERROR%b %s\n' "${RED}" "${NC}" "$*" >&2; exit 1; }

SKIP_PUBLISH=0
DRY_RUN=0
for arg in "$@"; do
    case "${arg}" in
        --skip-publish) SKIP_PUBLISH=1 ;;
        --dry-run)      DRY_RUN=1 ;;
        -h|--help)      sed -n '3,27p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)              fail "unknown argument: ${arg}" ;;
    esac
done

# ---------------------------------------------------------------------------
# 1. One version string, proven across every artifact.
# ---------------------------------------------------------------------------
read_toml_version() {
    # Reads project.version from a PEP 621 pyproject.toml.
    python3 - "$1" <<'PY'
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    print(tomllib.load(handle)["project"]["version"])
PY
}

VERSION="$(read_toml_version pyproject.toml)"
[[ -n "${VERSION}" ]] || fail "could not read the server version from pyproject.toml"

CLIENT_VERSION="$(read_toml_version client/pyproject.toml)"
[[ "${CLIENT_VERSION}" == "${VERSION}" ]] || \
    fail "client version ${CLIENT_VERSION} does not match server version ${VERSION}"

CHANGELOG_VERSION="$(sed -n '1s/.*(\([^)-]*\).*/\1/p' debian/changelog)"
[[ "${CHANGELOG_VERSION}" == "${VERSION}" ]] || \
    fail "debian/changelog version ${CHANGELOG_VERSION} does not match server version ${VERSION}"

info "release version ${VERSION} (server, client, changelog and image tag agree)"

# ---------------------------------------------------------------------------
# 2. Required tooling and credentials, checked before anything is built.
# ---------------------------------------------------------------------------
command -v docker >/dev/null || fail "docker is required"
python3 -c "import build" 2>/dev/null || fail "python -m build is required (pip install build)"

if [[ "${SKIP_PUBLISH}" -eq 0 ]]; then
    python3 -c "import twine" 2>/dev/null || fail "twine is required to publish (pip install twine)"
    # Presence only. The file is never read, printed, or passed on a command line.
    [[ -f "${HOME}/.pypirc" ]] || fail "~/.pypirc is required to publish the client without an interactive prompt"
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
    info "dry run: versions agree and tooling is present; nothing was built"
    exit 0
fi

# ---------------------------------------------------------------------------
# 3. Docker image, Docker Hub push and the Debian package.
#    Credentials are sourced from .env inside release_build.sh and never echoed.
# ---------------------------------------------------------------------------
info "building the image and the Debian package"
"${ROOT}/scripts/release_build.sh" "${VERSION}"

# ---------------------------------------------------------------------------
# 4. Python distributions: the server package and the client package.
# ---------------------------------------------------------------------------
info "building the server distribution"
rm -rf "${ROOT}/dist"
python3 -m build --outdir "${ROOT}/dist" "${ROOT}"

info "building the client distribution"
rm -rf "${ROOT}/client/dist"
python3 -m build --outdir "${ROOT}/client/dist" "${ROOT}/client"

# ---------------------------------------------------------------------------
# 5. Publish the client to PyPI.
# ---------------------------------------------------------------------------
if [[ "${SKIP_PUBLISH}" -eq 1 ]]; then
    warn "--skip-publish: the client was built but not uploaded to PyPI"
    info "release ${VERSION} built"
    exit 0
fi

info "publishing lmrs-client ${VERSION} to PyPI"
# twine reads ~/.pypirc itself; --non-interactive makes a missing or wrong
# credential fail loudly instead of blocking on a prompt in an automated run.
python3 -m twine upload --non-interactive "${ROOT}/client/dist/"*

info "release ${VERSION} complete: image pushed, packages built, client published"
