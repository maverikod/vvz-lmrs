#!/usr/bin/env bash
#
# Release pipeline: build & push the LMRS Docker image to Docker Hub, then build
# the Debian package that ships a systemd-managed container of that image.
# Version comes from pyproject.toml (or an explicit argument).
#
# Image: ${LMRS_DOCKER_REGISTRY:-vasilyvz}/${LMRS_DOCKER_IMAGE_NAME:-lmrs}:<VERSION>
#
# Usage:
#   ./build.sh                              # full: build + push + deb
#   ./scripts/release_build.sh 0.1.0        # explicit version
#   ./scripts/release_build.sh --docker-only   # build + push image only
#   ./scripts/release_build.sh --deb-only      # build .deb only (verifies image on Hub)
#   ./scripts/release_build.sh --skip-push     # build image + deb against the LOCAL image (dev)
#
# Author: Vasiliy Zdanovskiy
# email: vasilyvz@gmail.com
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
error() { echo -e "${RED}ERROR:${NC} $1" >&2; exit 1; }
info()  { echo -e "${GREEN}INFO:${NC} $1"; }
warn()  { echo -e "${YELLOW}WARN:${NC} $1"; }

REGISTRY="${LMRS_DOCKER_REGISTRY:-vasilyvz}"
IMAGE_NAME="${LMRS_DOCKER_IMAGE_NAME:-lmrs}"
VLLM_BASE="${LMRS_VLLM_BASE:-vllm/vllm-openai:latest}"

VERSION=""
DO_DOCKER=1
DO_DEB=1
SKIP_PUSH=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --deb-only) DO_DOCKER=0 ;;
        --docker-only) DO_DEB=0 ;;
        --skip-push|--skip-docker-push) SKIP_PUSH=1 ;;
        -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
        -*) error "Unknown option: $1" ;;
        *) [[ -n "${VERSION}" ]] && error "Unexpected argument: $1"; VERSION="$1" ;;
    esac
    shift
done

if [[ -z "${VERSION}" ]]; then
    VERSION="$(python3 - <<'PY'
import re, pathlib
text = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
print(m.group(1) if m else "")
PY
)"
    [[ -n "${VERSION}" ]] || error "could not read version from pyproject.toml"
    info "Using version ${VERSION} from pyproject.toml"
fi

[[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.+~]+)?$ ]] \
    || error "VERSION must look like a semver release tag (got: ${VERSION})"

FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:${VERSION}"
command -v docker >/dev/null 2>&1 || error "docker not found"

if (( DO_DOCKER )); then
    info "docker build ${FULL_IMAGE} (base ${VLLM_BASE})"
    docker build \
        -f docker/lmrs/Dockerfile \
        --build-arg VERSION="${VERSION}" \
        --build-arg VLLM_BASE="${VLLM_BASE}" \
        -t "${FULL_IMAGE}" \
        .
    if (( SKIP_PUSH )); then
        warn "skipping docker push (--skip-push)"
    else
        docker info 2>/dev/null | grep -qi "Username:" \
            || warn "no docker login detected; if push fails run: docker login"
        info "docker push ${FULL_IMAGE}"
        docker push "${FULL_IMAGE}"
    fi
fi

# Record the image ref consumed by the .deb at install time.
echo "${FULL_IMAGE}" > debian/lmrs-docker-image
info "wrote debian/lmrs-docker-image = ${FULL_IMAGE}"

if (( ! DO_DEB )); then
    info "done (docker-only)"
    exit 0
fi

# Never ship a .deb referencing an image that cannot be pulled at install time.
if (( ! SKIP_PUSH )); then
    info "verifying ${FULL_IMAGE} is reachable on the registry"
    docker manifest inspect "${FULL_IMAGE}" >/dev/null 2>&1 \
        || error "image not found on registry: ${FULL_IMAGE} (push first, or use --skip-push for local dev)"
fi

command -v dpkg-buildpackage >/dev/null 2>&1 \
    || error "dpkg-buildpackage not found (install: dpkg-dev debhelper)"

info "generating debian/changelog (${VERSION}-1)"
cat > debian/changelog <<EOF
lmrs (${VERSION}-1) unstable; urgency=medium

  * Release ${VERSION}: containerized LMRS (vLLM + LMCache + adapter server).

 -- Vasiliy Zdanovskiy <vasilyvz@gmail.com>  $(date -R)
EOF

info "dpkg-buildpackage -us -uc -b"
dpkg-buildpackage -us -uc -b

info "done; the .deb is in the parent directory"
