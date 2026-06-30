#!/usr/bin/env bash
#
# One-command release build: build the LMRS Docker image, push it to Docker Hub,
# and build the Debian package. Version is taken from pyproject.toml.
# Thin wrapper over scripts/release_build.sh (see it for flags: --docker-only,
# --deb-only, --skip-push).
#
# Author: Vasiliy Zdanovskiy
# email: vasilyvz@gmail.com
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scripts/release_build.sh" "$@"
