#!/bin/sh
set -e

ROOT=${1:?ROOT is required}
DESTDIR="$ROOT/debian/lmrs-container"

install -d "$DESTDIR/usr/lib/lmrs/bin" "$DESTDIR/usr/share/lmrs" "$DESTDIR/lib/systemd/system"
install -m 0755 "$ROOT/packaging/bin/lmrs-container" "$DESTDIR/usr/lib/lmrs/bin/lmrs-container"
install -m 0644 "$ROOT/packaging/config.json.template" "$DESTDIR/usr/share/lmrs/config.json.template"
install -m 0644 "$ROOT/packaging/lmrs.default.template" "$DESTDIR/usr/share/lmrs/lmrs.default.template"
install -m 0644 "$ROOT/packaging/lmcache.yaml.template" "$DESTDIR/usr/share/lmrs/lmcache.yaml.template"
install -m 0644 "$ROOT/debian/lmrs-docker-image" "$DESTDIR/usr/share/lmrs/docker-image"
install -m 0644 "$ROOT/packaging/systemd/lmrs.service" "$DESTDIR/lib/systemd/system/lmrs.service"
