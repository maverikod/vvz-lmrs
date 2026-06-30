#!/usr/bin/env bash
# Stage packaged files into the debian build tree (debian/lmrs). Invoked from
# debian/rules override_dh_auto_install.
#
# Author: Vasiliy Zdanovskiy
# email: vasilyvz@gmail.com
set -euo pipefail

ROOT="$1"
DEST="${ROOT}/debian/lmrs"

# Container helper.
install -d "${DEST}/usr/lib/lmrs/bin"
install -m 0755 "${ROOT}/packaging/bin/lmrs-container" "${DEST}/usr/lib/lmrs/bin/lmrs-container"

# systemd unit.
install -d "${DEST}/lib/systemd/system"
install -m 0644 "${ROOT}/packaging/systemd/lmrs.service" "${DEST}/lib/systemd/system/lmrs.service"

# Templates (config + /etc/default), installed under /usr/share for postinst.
install -d "${DEST}/usr/share/lmrs"
install -m 0644 "${ROOT}/packaging/config.json.template" "${DEST}/usr/share/lmrs/config.json.template"
install -m 0644 "${ROOT}/packaging/lmrs.default.template" "${DEST}/usr/share/lmrs/lmrs.default.template"

# Docker image reference (written by scripts/release_build.sh). Ship a
# placeholder if building the .deb standalone.
if [ -f "${ROOT}/debian/lmrs-docker-image" ]; then
    install -m 0644 "${ROOT}/debian/lmrs-docker-image" "${DEST}/usr/share/lmrs/docker-image"
else
    install -d "${DEST}/usr/share/lmrs"
    printf 'vasilyvz/lmrs:latest\n' > "${DEST}/usr/share/lmrs/docker-image"
    chmod 0644 "${DEST}/usr/share/lmrs/docker-image"
fi
