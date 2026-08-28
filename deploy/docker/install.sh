#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TARGET_DIR="${REELAY_INSTALL_DIR:-/opt/reelay}"
MARKER="${TARGET_DIR}/.reelay-managed"

die() {
    echo "Reelay install failed: $*" >&2
    exit 1
}

if [[ "$(uname -s)" != "Linux" ]]; then
    die "Linux is required"
fi
if [[ "$(id -u)" -ne 0 ]]; then
    die "run this installer with sudo"
fi
if [[ "${TARGET_DIR}" != /* || "${TARGET_DIR}" == "/" ]]; then
    die "REELAY_INSTALL_DIR must be a safe absolute path"
fi
command -v docker >/dev/null 2>&1 || die "Docker is not installed"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is not installed"
docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"

for required in Dockerfile compose.yaml .env data/reelay.db BUNDLE_INFO.txt; do
    [[ -e "${SOURCE_DIR}/${required}" ]] || die "bundle is missing ${required}"
done

[[ ! -L "${TARGET_DIR}" ]] || die "${TARGET_DIR} must not be a symbolic link"
bundle_commit="$(awk '/^Git commit: / {print $3; exit}' "${SOURCE_DIR}/BUNDLE_INFO.txt")"
[[ "${bundle_commit}" =~ ^[0-9a-f]{40}$ ]] || die "bundle commit metadata is invalid"

if [[ -e "${MARKER}" ]]; then
    [[ "$(stat -c '%u' "${TARGET_DIR}")" == "0" ]] \
        || die "managed target is not owned by root"
    [[ "$(cat "${MARKER}")" == "${bundle_commit}" ]] \
        || die "a different Reelay version is already installed in ${TARGET_DIR}"
elif [[ -e "${TARGET_DIR}" ]]; then
    if find "${TARGET_DIR}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | grep -q .; then
        die "${TARGET_DIR} is not empty; move it away and run the installer again"
    fi
fi

if [[ ! -e "${MARKER}" ]]; then
    install -d -m 0755 "${TARGET_DIR}"
    cp -a "${SOURCE_DIR}/." "${TARGET_DIR}/"
    printf '%s\n' "${bundle_commit}" >"${MARKER}"
fi

chown -R root:root "${TARGET_DIR}"
chmod -R go-w "${TARGET_DIR}"
chmod 0600 "${TARGET_DIR}/.env"
chown root:root "${TARGET_DIR}/.env"
chown -R 10001:10001 "${TARGET_DIR}/data"
find "${TARGET_DIR}/data" -type d -exec chmod 0700 {} +
find "${TARGET_DIR}/data" -type f -exec chmod 0600 {} +

cd "${TARGET_DIR}"
docker compose up --detach --build --force-recreate reelay

container_id="$(docker compose ps --quiet reelay)"
[[ -n "${container_id}" ]] || die "Compose did not create the Reelay container"

for _ in $(seq 1 90); do
    state="$(docker inspect --format '{{.State.Status}}' "${container_id}" 2>/dev/null || true)"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${container_id}" 2>/dev/null || true)"
    if [[ "${state}" == "running" && "${health}" == "healthy" ]]; then
        echo "Reelay is installed and healthy."
        echo "Location: ${TARGET_DIR}"
        echo "Logs: cd ${TARGET_DIR} && docker compose logs --follow --tail=100 reelay"
        exit 0
    fi
    if [[ "${state}" == "exited" || "${state}" == "dead" ]]; then
        break
    fi
    sleep 2
done

docker compose ps >&2 || true
die "container did not become healthy; inspect logs locally in ${TARGET_DIR}"
