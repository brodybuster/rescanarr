#!/bin/sh
set -eu

APP_USER="appuser"
APP_GROUP="appgroup"
APP_HOME="/app"

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
TZ_VALUE="${TZ:-UTC}"

log() {
    TZ="${TZ_VALUE}" date '+[%Y-%m-%dT%H:%M:%S%:z]'" $*"
}

log "Starting rescanarr entrypoint"
log "Requested UID:GID = ${PUID}:${PGID}"
log "Requested TZ = ${TZ_VALUE}"

if [ "$(id -u)" != "0" ]; then
  log "Container is not running as root; cannot remap UID/GID. Starting as current user."
  exec "$@"
fi

if [ -n "${TZ_VALUE}" ] && [ -f "/usr/share/zoneinfo/${TZ_VALUE}" ]; then
  ln -snf "/usr/share/zoneinfo/${TZ_VALUE}" /etc/localtime
  echo "${TZ_VALUE}" > /etc/timezone
else
  log "Warning: timezone '${TZ_VALUE}' not found; leaving default timezone in place"
fi

CURRENT_GID="$(getent group "${APP_GROUP}" | cut -d: -f3)"
CURRENT_UID="$(id -u "${APP_USER}")"

if [ "${CURRENT_GID}" != "${PGID}" ]; then
  groupmod -o -g "${PGID}" "${APP_GROUP}"
fi

if [ "${CURRENT_UID}" != "${PUID}" ]; then
  usermod -o -u "${PUID}" -g "${PGID}" "${APP_USER}"
fi

mkdir -p /config/logs

chown -R "${PUID}:${PGID}" /config || true
chown -R "${PUID}:${PGID}" /app || true

log "Launching as ${APP_USER} (${PUID}:${PGID})"
exec gosu "${PUID}:${PGID}" "$@"
