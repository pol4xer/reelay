#!/usr/bin/env bash
set -euo pipefail

LABEL="com.pol4xer.reelay"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"
LOG_DIR="${PROJECT_ROOT}/data/logs"
TEMPLATE_PATH="${PROJECT_ROOT}/deploy/macos/${LABEL}.plist.template"
PLIST_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${PLIST_DIR}/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
SERVICE="${DOMAIN}/${LABEL}"

usage() {
    echo "Usage: $0 {install|start|stop|status|uninstall}" >&2
    exit 2
}

escape_sed_replacement() {
    printf "%s" "$1" | sed -e 's/[&|]/\\&/g'
}

is_loaded() {
    launchctl print "${SERVICE}" >/dev/null 2>&1
}

bootstrap_service() {
    local attempt
    for attempt in 1 2 3; do
        if launchctl bootstrap "${DOMAIN}" "${PLIST_PATH}"; then
            return
        fi
        if is_loaded; then
            return
        fi
        sleep 1
    done
    echo "Failed to bootstrap ${LABEL} after 3 attempts." >&2
    exit 1
}

assert_idle() {
    local database="${PROJECT_ROOT}/data/reelay.db"
    if [[ -f "${database}" ]] && command -v sqlite3 >/dev/null 2>&1; then
        if [[ "$(sqlite3 "${database}" "SELECT COUNT(*) FROM jobs WHERE status IN ('downloading', 'publishing');")" != "0" ]]; then
            echo "Refusing to stop or restart: a download/publication is in progress." >&2
            exit 1
        fi
    fi
}

preflight() {
    if [[ ! -x "${PYTHON_BIN}" ]]; then
        echo "Python environment is missing: ${PYTHON_BIN}" >&2
        echo "Run 'make install' first." >&2
        exit 1
    fi
    if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
        echo "Configuration is missing: ${PROJECT_ROOT}/.env" >&2
        exit 1
    fi
    for command in ffmpeg ffprobe; do
        if ! command -v "${command}" >/dev/null 2>&1; then
            echo "Required command is missing: ${command}" >&2
            exit 1
        fi
    done
    if grep -q '^PUBLISH_THREADS=true$' "${PROJECT_ROOT}/.env"; then
        if [[ ! -x "${PROJECT_ROOT}/data/bin/cloudflared" ]] \
            && ! command -v cloudflared >/dev/null 2>&1; then
            echo "cloudflared is required while PUBLISH_THREADS=true." >&2
            exit 1
        fi
    fi
}

render_plist() {
    local temporary_plist
    local project_root
    local python_bin
    local log_dir

    mkdir -p "${PLIST_DIR}" "${LOG_DIR}"

    project_root="$(escape_sed_replacement "${PROJECT_ROOT}")"
    python_bin="$(escape_sed_replacement "${PYTHON_BIN}")"
    log_dir="$(escape_sed_replacement "${LOG_DIR}")"
    temporary_plist="$(mktemp "${PLIST_PATH}.XXXXXX")"

    sed \
        -e "s|__PROJECT_ROOT__|${project_root}|g" \
        -e "s|__PYTHON_BIN__|${python_bin}|g" \
        -e "s|__LOG_DIR__|${log_dir}|g" \
        "${TEMPLATE_PATH}" >"${temporary_plist}"

    plutil -lint "${temporary_plist}" >/dev/null
    chmod 0644 "${temporary_plist}"
    mv "${temporary_plist}" "${PLIST_PATH}"
}

install_service() {
    preflight
    assert_idle
    if [[ ! -f "${TEMPLATE_PATH}" ]]; then
        echo "LaunchAgent template is missing: ${TEMPLATE_PATH}" >&2
        exit 1
    fi

    render_plist
    if is_loaded; then
        launchctl bootout "${SERVICE}"
        sleep 1
    fi
    bootstrap_service
    launchctl enable "${SERVICE}"

    echo "Installed and started ${LABEL}."
    echo "Logs: ${LOG_DIR}"
}

start_service() {
    preflight
    assert_idle
    if [[ ! -f "${PLIST_PATH}" ]]; then
        echo "LaunchAgent is not installed. Run 'make service-install' first." >&2
        exit 1
    fi

    if is_loaded; then
        launchctl kickstart -k "${SERVICE}"
    else
        bootstrap_service
    fi

    echo "Started ${LABEL}."
}

uninstall_service() {
    assert_idle
    if is_loaded; then
        launchctl bootout "${SERVICE}"
    fi
    if [[ -f "${PLIST_PATH}" ]]; then
        rm "${PLIST_PATH}"
    fi
    echo "Uninstalled ${LABEL}."
}

stop_service() {
    assert_idle
    if is_loaded; then
        launchctl bootout "${SERVICE}"
        echo "Stopped ${LABEL}."
    else
        echo "${LABEL} is already stopped."
    fi
}

status_service() {
    echo "LaunchAgent: ${PLIST_PATH}"
    echo "Standard output: ${LOG_DIR}/reelay.log"
    echo "Standard error: ${LOG_DIR}/reelay.error.log"

    if is_loaded; then
        launchctl print "${SERVICE}"
    else
        echo "State: stopped"
    fi
}

case "${1:-}" in
    install)
        install_service
        ;;
    start)
        start_service
        ;;
    stop)
        stop_service
        ;;
    status)
        status_service
        ;;
    uninstall)
        uninstall_service
        ;;
    *)
        usage
        ;;
esac
