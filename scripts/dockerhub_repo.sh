#!/usr/bin/env bash
# Resolve Docker Hub image repository for LMRS builds.
# shellcheck shell=bash

# Priority: LMRS_DOCKERHUB_REPO > LMRS_DOCKER_REGISTRY/LMRS_DOCKER_IMAGE_NAME > docker logged-in Username > LMRS_DOCKERHUB_USERNAME > vasilyvz

dockerhub_repo_default() {
  if [ -n "${LMRS_DOCKERHUB_REPO:-}" ]; then
    printf '%s\n' "$LMRS_DOCKERHUB_REPO"
    return 0
  fi

  local image_name="${LMRS_DOCKER_IMAGE_NAME:-lmrs}"
  if [ -n "${LMRS_DOCKER_REGISTRY:-}" ]; then
    printf '%s/%s\n' "$LMRS_DOCKER_REGISTRY" "$image_name"
    return 0
  fi

  local user=""
  if command -v docker >/dev/null 2>&1; then
    user="$(docker info 2>/dev/null | sed -n 's/^ Username: //p' | head -1 | tr -d '[:space:]')"
  fi
  if [ -z "$user" ] && [ -n "${LMRS_DOCKERHUB_USERNAME:-}" ]; then
    user="$LMRS_DOCKERHUB_USERNAME"
  fi
  if [ -z "$user" ]; then
    user="vasilyvz"
  fi
  printf '%s/%s\n' "$user" "$image_name"
}

# Echo Docker Hub namespace of the current docker CLI session (may be empty).
dockerhub_logged_in_user() {
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi
  docker info 2>/dev/null | sed -n 's/^ Username: //p' | head -1 | tr -d '[:space:]'
}
