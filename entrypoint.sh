#!/bin/sh
set -eu

APP_USER="appuser"
APP_GROUP="appgroup"
APP_UID="${PUID:-1000}"
APP_GID="${PGID:-1000}"
APP_TZ="${TZ:-UTC}"

echo "Starting rescanarr entrypoint"
echo "Requested UID:GID = ${APP_UID}:${APP_GID}"
echo "Requested TZ = ${APP_TZ}"

if [ "$(id -u)" != "0" ]; then
  echo "Container is not running as root; cannot remap UID/GID. Starting as current user."
  exec "$@"
fi

if [ -n "${APP_TZ}" ] && [ -f "/usr/share/zoneinfo/${APP_TZ}" ]; then
  ln -snf "/usr/share/zoneinfo/${APP_TZ}" /etc/localtime
  echo "${APP_TZ}" > /etc/timezone
else
  echo "Warning: timezone '${APP_TZ}' not found; leaving default timezone in place"
fi

CURRENT_GID="$(getent group "${APP_GROUP}" | cut -d: -f3)"
CURRENT_UID="$(id -u "${APP_USER}")"

if [ "${CURRENT_GID}" != "${APP_GID}" ]; then
  groupmod -o -g "${APP_GID}" "${APP_GROUP}"
fi

if [ "${CURRENT_UID}" != "${APP_UID}" ]; then
  usermod -o -u "${APP_UID}" -g "${APP_GID}" "${APP_USER}"
fi

mkdir -p /config/logs

chown -R "${APP_UID}:${APP_GID}" /config || true
chown -R "${APP_UID}:${APP_GID}" /app || true

echo "Launching as ${APP_USER} (${APP_UID}:${APP_GID})"
exec gosu "${APP_UID}:${APP_GID}" "$@"
