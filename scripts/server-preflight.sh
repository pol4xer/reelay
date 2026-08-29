#!/usr/bin/env bash

# Reelay Linux server preflight.
# This script does not install packages or read application credentials.
# It only creates a temporary directory under /tmp and removes it on exit.

set -uo pipefail

SCRIPT_VERSION="1.1.0"
DATA_DIR="${1:-/opt/reelay/data}"
MIN_CPU=2
MIN_MEMORY_KB=$((2 * 1024 * 1024))
MIN_DISK_KB=$((10 * 1024 * 1024))
MIN_TMP_KB=$((2 * 1024 * 1024))
TMP_BASE="/tmp"

failures=0
warnings=0
temporary_directory=""

cleanup() {
    case "${temporary_directory:-}" in
        /tmp/reelay-preflight.*)
            [[ -d "${temporary_directory}" ]] && rm -rf -- "${temporary_directory}"
            ;;
    esac
}
trap cleanup EXIT INT TERM

if [[ $# -gt 1 ]]; then
    printf 'Usage: %s [DATA_DIR]\n' "$0" >&2
    exit 2
fi
if [[ "${DATA_DIR}" != /* ]]; then
    printf 'DATA_DIR must be an absolute path: %s\n' "${DATA_DIR}" >&2
    exit 2
fi

section() {
    printf '\n=== %s ===\n' "$1"
}

value() {
    printf '%-30s %s\n' "$1" "$2"
}

pass() {
    printf '[PASS] %s\n' "$1"
}

warn() {
    warnings=$((warnings + 1))
    printf '[WARN] %s\n' "$1"
}

fail() {
    failures=$((failures + 1))
    printf '[FAIL] %s\n' "$1"
}

command_version() {
    local command_name="$1"
    shift
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        value "${command_name}" "missing"
        return 1
    fi

    local version
    version="$("${command_name}" "$@" 2>&1 | head -n 1)"
    value "${command_name}" "${version:-installed}"
}

disk_available_kb() {
    df -Pk "$1" 2>/dev/null | awk 'NR == 2 {print $4}'
}

filesystem_type() {
    if command -v findmnt >/dev/null 2>&1; then
        findmnt -n -o FSTYPE -T "$1" 2>/dev/null | head -n 1
    else
        df -PT "$1" 2>/dev/null | awk 'NR == 2 {print $2}'
    fi
}

nearest_existing_parent() {
    local candidate="$1"
    while [[ ! -d "${candidate}" && "${candidate}" != "/" ]]; do
        candidate="$(dirname "${candidate}")"
    done
    printf '%s\n' "${candidate}"
}

human_kb() {
    local kilobytes="${1:-0}"
    awk -v kb="${kilobytes}" 'BEGIN {
        if (kb >= 1048576) printf "%.1f GiB", kb / 1048576;
        else if (kb >= 1024) printf "%.1f MiB", kb / 1024;
        else printf "%d KiB", kb;
    }'
}

effective_cpu() {
    local host_cpu="$1"
    local quota period limited
    if [[ -r /sys/fs/cgroup/cpu.max ]]; then
        read -r quota period </sys/fs/cgroup/cpu.max || true
        if [[ "${quota:-max}" != "max" && "${period:-0}" =~ ^[0-9]+$ && "${period}" -gt 0 ]]; then
            limited="$(awk -v quota="${quota}" -v period="${period}" 'BEGIN {
                result = quota / period;
                if (result < 1) result = 1;
                printf "%.2f", result;
            }')"
            awk -v host="${host_cpu}" -v limited="${limited}" 'BEGIN {
                if (limited < host) print limited; else print host;
            }'
            return
        fi
    fi
    printf '%s\n' "${host_cpu}"
}

effective_memory_kb() {
    local host_memory_kb="$1"
    local limit_bytes limit_kb
    if [[ -r /sys/fs/cgroup/memory.max ]]; then
        limit_bytes="$(cat /sys/fs/cgroup/memory.max 2>/dev/null || true)"
        if [[ "${limit_bytes}" =~ ^[0-9]+$ ]]; then
            limit_kb=$((limit_bytes / 1024))
            if ((limit_kb < host_memory_kb)); then
                printf '%s\n' "${limit_kb}"
                return
            fi
        fi
    fi
    printf '%s\n' "${host_memory_kb}"
}

check_dns() {
    local host="$1"
    if command -v getent >/dev/null 2>&1 && getent ahosts "${host}" >/dev/null 2>&1; then
        pass "DNS ${host}"
        return
    fi
    if command -v dig >/dev/null 2>&1 && dig +short "${host}" 2>/dev/null | grep -q .; then
        pass "DNS ${host}"
        return
    fi
    if command -v nslookup >/dev/null 2>&1 && nslookup "${host}" >/dev/null 2>&1; then
        pass "DNS ${host}"
        return
    fi
    fail "DNS lookup failed: ${host}"
}

check_https() {
    local label="$1"
    local url="$2"
    local output_file error_file status curl_status

    output_file="${temporary_directory}/https-output"
    error_file="${temporary_directory}/https-error"
    status="$(
        curl \
            --silent \
            --show-error \
            --location \
            --max-redirs 3 \
            --connect-timeout 10 \
            --max-time 20 \
            --proto '=https' \
            --proto-redir '=https' \
            --tlsv1.2 \
            --range 0-0 \
            --user-agent "Reelay-Server-Preflight/${SCRIPT_VERSION}" \
            --output "${output_file}" \
            --write-out '%{http_code}' \
            "${url}" \
            2>"${error_file}"
    )"
    curl_status=$?

    if [[ ${curl_status} -eq 0 && "${status}" =~ ^[1-5][0-9][0-9]$ ]]; then
        pass "HTTPS ${label}: HTTP ${status}"
    else
        local reason
        reason="$(tail -n 1 "${error_file}" 2>/dev/null || true)"
        fail "HTTPS ${label}: ${reason:-HTTP ${status:-000}}"
    fi
}

tcp_reachable() {
    local host="$1"
    local port="$2"
    if command -v nc >/dev/null 2>&1; then
        nc -z -w 7 "${host}" "${port}" >/dev/null 2>&1
        return
    fi
    if command -v timeout >/dev/null 2>&1; then
        timeout 7 bash -c "exec 3<>/dev/tcp/${host}/${port}" >/dev/null 2>&1
        return
    fi
    return 2
}

check_cloudflare_tunnel() {
    tcp_reachable region1.v2.argotunnel.com 7844
    local first_status=$?
    if [[ ${first_status} -eq 0 ]]; then
        pass "TCP Cloudflare Tunnel fallback: region1.v2.argotunnel.com:7844"
        return
    fi
    tcp_reachable region2.v2.argotunnel.com 7844
    local second_status=$?
    if [[ ${second_status} -eq 0 ]]; then
        pass "TCP Cloudflare Tunnel fallback: region2.v2.argotunnel.com:7844"
        return
    fi
    if [[ ${first_status} -eq 2 && ${second_status} -eq 2 ]]; then
        warn "Cannot test Cloudflare Tunnel TCP fallback: install nc or timeout"
    else
        warn "Cloudflare Tunnel TCP 7844 fallback is blocked; QUIC may still work"
    fi
}

printf 'Reelay server preflight v%s\n' "${SCRIPT_VERSION}"
printf 'Generated: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf 'This report contains no Reelay credentials.\n'

temporary_directory="$(mktemp -d "${TMP_BASE}/reelay-preflight.XXXXXX" 2>/dev/null || true)"
if [[ -z "${temporary_directory}" || ! -d "${temporary_directory}" ]]; then
    fail "Cannot create a temporary directory under /tmp"
    section "RESULT"
    value "failures" "${failures}"
    value "warnings" "${warnings}"
    value "REELAY_SERVER_RESULT" "NOT_READY"
    exit 1
else
    pass "Temporary directory is writable and will be removed"
fi

section "IDENTITY AND SSH"
value "hostname" "$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo unknown)"
value "current_user" "$(id -un 2>/dev/null || echo unknown)"
value "uid/gid" "$(id 2>/dev/null || echo unknown)"
if [[ -n "${SSH_CONNECTION:-}" ]]; then
    read -r ssh_client_ip ssh_client_port ssh_server_ip ssh_server_port <<<"${SSH_CONNECTION}"
    value "ssh_client" "${ssh_client_ip}:${ssh_client_port}"
    value "ssh_server" "${ssh_server_ip}:${ssh_server_port}"
    pass "Script is running inside an SSH session"
else
    value "ssh_connection" "not detected"
    warn "SSH endpoint must be supplied separately"
fi
if command -v ssh-keygen >/dev/null 2>&1; then
    host_key_found=false
    for host_key in \
        /etc/ssh/ssh_host_ed25519_key.pub \
        /etc/ssh/ssh_host_ecdsa_key.pub \
        /etc/ssh/ssh_host_rsa_key.pub; do
        if [[ -r "${host_key}" ]]; then
            value "ssh_host_key" "$(ssh-keygen -l -E sha256 -f "${host_key}" 2>/dev/null || echo unreadable)"
            host_key_found=true
        fi
    done
    if [[ "${host_key_found}" == "false" ]]; then
        value "ssh_host_key" "not readable; request the public fingerprint from the owner"
    fi
fi

if [[ "$(id -u 2>/dev/null || echo 1)" -eq 0 ]]; then
    value "privilege" "root"
    pass "Initial package and systemd setup is allowed"
elif command -v sudo >/dev/null 2>&1; then
    value "sudo" "installed; permission was not probed"
    pass "sudo is installed; the owner may need to approve privileged setup"
else
    value "sudo" "missing"
    warn "No root or sudo access; Docker/user-space deployment may be required"
fi

section "OPERATING SYSTEM"
operating_system="$(uname -s 2>/dev/null || echo unknown)"
value "operating_system" "${operating_system}"
if [[ "${operating_system}" == "Linux" ]]; then
    pass "Linux host detected"
else
    fail "Reelay server deployment expects Linux, got ${operating_system}"
fi
if [[ -r /etc/os-release ]]; then
    os_pretty="$(awk -F= '$1 == "PRETTY_NAME" {value=$0; sub(/^[^=]*=/, "", value); gsub(/^"|"$/, "", value); print value; exit}' /etc/os-release)"
    os_id="$(awk -F= '$1 == "ID" {value=$2; gsub(/^"|"$/, "", value); print value; exit}' /etc/os-release)"
    os_version="$(awk -F= '$1 == "VERSION_ID" {value=$2; gsub(/^"|"$/, "", value); print value; exit}' /etc/os-release)"
    value "os" "${os_pretty:-unknown}"
    value "os_id" "${os_id:-unknown} ${os_version:-}"
else
    value "os" "unknown"
    warn "/etc/os-release is unavailable"
fi
value "kernel" "$(uname -sr 2>/dev/null || echo unknown)"
if libc="$(getconf GNU_LIBC_VERSION 2>/dev/null)"; then
    value "libc" "${libc}"
elif command -v ldd >/dev/null 2>&1; then
    value "libc" "$(ldd --version 2>&1 | head -n 1)"
else
    value "libc" "unknown"
fi
architecture="$(uname -m 2>/dev/null || echo unknown)"
value "architecture" "${architecture}"
case "${architecture}" in
    x86_64 | amd64 | aarch64 | arm64)
        pass "Architecture is supported"
        ;;
    *)
        fail "Unsupported or unverified architecture: ${architecture}"
        ;;
esac

if command -v systemd-detect-virt >/dev/null 2>&1; then
    value "virtualization" "$(systemd-detect-virt 2>/dev/null || echo none)"
fi
if [[ -f /.dockerenv ]]; then
    value "container" "docker"
    warn "The script is running inside a container, not on the server host"
fi

section "CPU AND MEMORY"
host_cpu="$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 1)"
cpu_available="$(effective_cpu "${host_cpu}")"
value "host_cpu_threads" "${host_cpu}"
value "effective_cpu" "${cpu_available}"
if awk -v cpu="${cpu_available}" -v minimum="${MIN_CPU}" 'BEGIN {exit !(cpu >= minimum)}'; then
    pass "CPU capacity is sufficient (${cpu_available} >= ${MIN_CPU})"
else
    warn "CPU is below recommendation (${cpu_available} < ${MIN_CPU}); ffmpeg will be slow"
fi

host_memory_kb="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || echo 0)"
memory_available_kb="$(effective_memory_kb "${host_memory_kb:-0}")"
value "host_memory" "$(human_kb "${host_memory_kb:-0}")"
value "effective_memory" "$(human_kb "${memory_available_kb:-0}")"
value "swap" "$(awk '/^SwapTotal:/ {print $2 " KiB"; exit}' /proc/meminfo 2>/dev/null || echo unknown)"
if ((memory_available_kb >= MIN_MEMORY_KB)); then
    pass "Memory is sufficient ($(human_kb "${memory_available_kb}"))"
else
    warn "Memory is below 2 GiB; ffmpeg may be killed by the kernel"
fi

section "FILESYSTEM"
data_probe_path="$(nearest_existing_parent "${DATA_DIR}")"
value "requested_data_dir" "${DATA_DIR}"
value "data_probe_path" "${data_probe_path}"
if [[ -d "${DATA_DIR}" ]]; then
    pass "Requested data directory already exists"
else
    warn "Requested data directory does not exist yet; the owner must create it"
fi
if [[ -w "${data_probe_path}" ]]; then
    pass "Current user can write to the nearest existing data parent"
else
    warn "Current user cannot write to ${data_probe_path}; owner setup is required"
fi

for checked_path in "${data_probe_path}" "${TMP_BASE}"; do
    [[ -d "${checked_path}" ]] || continue
    available_kb="$(disk_available_kb "${checked_path}")"
    fs_type="$(filesystem_type "${checked_path}")"
    value "disk ${checked_path}" "$(human_kb "${available_kb:-0}") free; fs=${fs_type:-unknown}"
done

data_available_kb="$(disk_available_kb "${data_probe_path}")"
tmp_available_kb="$(disk_available_kb "${TMP_BASE}")"
if ((data_available_kb >= MIN_DISK_KB)); then
    pass "Data filesystem has at least 10 GiB free"
else
    fail "Data filesystem has less than 10 GiB free"
fi
if ((tmp_available_kb >= MIN_TMP_KB)); then
    pass "Temporary filesystem has at least 2 GiB free"
else
    fail "Temporary filesystem has less than 2 GiB free"
fi

data_fs="$(filesystem_type "${data_probe_path}")"
case "${data_fs}" in
    ext2 | ext3 | ext4 | xfs | btrfs | zfs)
        pass "Data filesystem ${data_fs} is suitable for single-node SQLite WAL"
        ;;
    nfs | nfs4 | cifs | smbfs | 9p | sshfs | fuse*)
        fail "Data filesystem ${data_fs} is unsuitable for SQLite WAL; use a local block volume"
        ;;
    tmpfs | ramfs)
        fail "Data filesystem ${data_fs} is ephemeral and must not store the queue"
        ;;
    overlay | overlayfs)
        warn "Data filesystem is overlay; mount a persistent volume at ${DATA_DIR}"
        ;;
    *)
        warn "Data filesystem ${data_fs:-unknown} needs manual persistence verification"
        ;;
esac

section "INIT AND PACKAGE MANAGEMENT"
systemd_active=false
if command -v systemctl >/dev/null 2>&1 && [[ "$(ps -p 1 -o comm= 2>/dev/null)" == "systemd" ]]; then
    systemd_active=true
    value "init" "systemd"
    command_version systemctl --version || true
    pass "systemd service deployment is available"
else
    value "init" "$(ps -p 1 -o comm= 2>/dev/null || echo unknown)"
    warn "systemd is not active; Docker or another supervisor will be needed"
fi

package_manager="none"
for candidate in apt-get dnf yum apk pacman; do
    if command -v "${candidate}" >/dev/null 2>&1; then
        package_manager="${candidate}"
        break
    fi
done
value "package_manager" "${package_manager}"
if [[ "${package_manager}" == "none" ]]; then
    warn "No supported package manager detected"
else
    pass "Package manager detected: ${package_manager}"
fi

section "REQUIRED TOOLS"
command_version bash --version || true
command_version curl --version || true
command_version git --version || true
command_version rsync --version || true
command_version tar --version || true
command_version sqlite3 --version || true
command_version python3 --version || true
command_version uv --version || true
command_version ffmpeg -version || true
command_version ffprobe -version || true
command_version cloudflared --version || true
command_version docker --version || true

if command -v cloudflared >/dev/null 2>&1; then
    if cloudflared --version >/dev/null 2>&1; then
        pass "cloudflared is executable on this architecture"
    else
        fail "cloudflared exists but cannot execute on this architecture"
    fi
else
    warn "Linux cloudflared must be installed before Threads publishing"
fi

if command -v docker >/dev/null 2>&1; then
    compose_version="$(docker compose version 2>/dev/null | head -n 1 || true)"
    value "docker compose" "${compose_version:-unavailable or not permitted}"
fi

docker_available=false
if command -v docker >/dev/null 2>&1; then
    docker_server_version="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
    if [[ -n "${docker_server_version}" ]]; then
        docker_available=true
        pass "Docker daemon is reachable (${docker_server_version})"
    else
        warn "Docker CLI exists but the daemon is unavailable or not permitted"
    fi
fi
if [[ "${systemd_active}" == "true" || "${docker_available}" == "true" ]]; then
    pass "At least one supported supervisor path is available"
else
    fail "Neither active systemd nor Docker is available"
fi

if command -v python3 >/dev/null 2>&1; then
    python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null || echo 0.0.0)"
    python_major="${python_version%%.*}"
    python_rest="${python_version#*.}"
    python_minor="${python_rest%%.*}"
    if [[ "${python_major}" =~ ^[0-9]+$ && "${python_minor}" =~ ^[0-9]+$ ]] \
        && ((python_major > 3 || (python_major == 3 && python_minor >= 13))); then
        pass "Python ${python_version} satisfies >=3.13"
    else
        warn "Python >=3.13 must be installed; found ${python_version}"
    fi
    if PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY' >/dev/null 2>&1
import sqlite3
from zoneinfo import ZoneInfo

ZoneInfo("Europe/Istanbul")
PY
    then
        pass "Python sqlite3 and Europe/Istanbul ZoneInfo are available"
    else
        warn "Python sqlite3/ZoneInfo probe failed"
    fi
fi

if command -v ffmpeg >/dev/null 2>&1; then
    encoders="$(ffmpeg -hide_banner -encoders 2>/dev/null || true)"
    if grep -q 'libx264' <<<"${encoders}"; then
        pass "ffmpeg includes libx264"
    else
        warn "ffmpeg does not advertise libx264"
    fi
    if grep -Eq '(^|[[:space:]])A.*[[:space:]]aac([[:space:]]|$)' <<<"${encoders}"; then
        pass "ffmpeg includes AAC encoding"
    else
        warn "ffmpeg does not advertise AAC encoding"
    fi
else
    warn "ffmpeg/ffprobe must be installed before deployment"
fi

if command -v sqlite3 >/dev/null 2>&1 && [[ -d "${temporary_directory}" ]]; then
    sqlite_test="${temporary_directory}/sqlite-wal-test.db"
    sqlite_result="$(
        sqlite3 "${sqlite_test}" \
            "PRAGMA journal_mode=WAL; CREATE TABLE probe(value TEXT); INSERT INTO probe VALUES('ok'); SELECT value FROM probe;" \
            2>/dev/null || true
    )"
    if grep -q 'ok' <<<"${sqlite_result}"; then
        pass "SQLite WAL runtime test succeeded under temporary storage"
    else
        fail "SQLite WAL write/read test failed under ${temporary_directory}"
    fi
else
    warn "SQLite WAL test skipped because sqlite3 is missing"
fi

section "CLOCK"
value "utc_time" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
value "local_time" "$(date '+%Y-%m-%dT%H:%M:%S%z')"
value "timezone" "$(cat /etc/timezone 2>/dev/null || readlink /etc/localtime 2>/dev/null || echo unknown)"
if [[ -e /usr/share/zoneinfo/Europe/Istanbul ]]; then
    pass "Europe/Istanbul timezone data is installed"
else
    warn "tzdata for Europe/Istanbul is missing"
fi
if command -v timedatectl >/dev/null 2>&1; then
    ntp_state="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo unknown)"
    value "ntp_synchronized" "${ntp_state:-unknown}"
    if [[ "${ntp_state}" == "yes" ]]; then
        pass "System clock is synchronized"
    else
        warn "NTP synchronization is not confirmed"
    fi
fi

section "DNS"
for host in \
    api.telegram.org \
    www.instagram.com \
    graph.facebook.com \
    rupload.facebook.com \
    graph.threads.net \
    api.trycloudflare.com \
    oauth2.googleapis.com \
    www.googleapis.com \
    www.tiktok.com \
    open.tiktokapis.com \
    open-upload.tiktokapis.com \
    region1.v2.argotunnel.com \
    region2.v2.argotunnel.com; do
    check_dns "${host}"
done

section "HTTPS EGRESS"
if ! command -v curl >/dev/null 2>&1; then
    fail "curl is required to test outbound HTTPS"
else
    check_https "Telegram" "https://api.telegram.org/"
    check_https "Instagram" "https://www.instagram.com/"
    check_https "Meta Graph" "https://graph.facebook.com/"
    check_https "Meta Upload" "https://rupload.facebook.com/"
    check_https "Threads Graph" "https://graph.threads.net/"
    check_https "Cloudflare Quick Tunnel API" "https://api.trycloudflare.com/"
    check_https "Google OAuth" "https://oauth2.googleapis.com/"
    check_https "YouTube API" "https://www.googleapis.com/youtube/v3/"
    check_https "TikTok OAuth" "https://www.tiktok.com/"
    check_https "TikTok Content API" "https://open.tiktokapis.com/"
    check_https "TikTok Upload" "https://open-upload.tiktokapis.com/"
    check_https "Cloudflare" "https://www.cloudflare.com/cdn-cgi/trace"
fi
check_cloudflare_tunnel

section "RESULT"
value "failures" "${failures}"
value "warnings" "${warnings}"
if ((failures > 0)); then
    value "REELAY_SERVER_RESULT" "NOT_READY"
    printf 'Fix the FAIL items, or send this entire report back for review.\n'
elif ((warnings > 0)); then
    value "REELAY_SERVER_RESULT" "READY_WITH_WARNINGS"
    printf 'The server can probably be used; send this entire report back for review.\n'
else
    value "REELAY_SERVER_RESULT" "READY"
    printf 'The server satisfies the Reelay preflight.\n'
fi

if ((failures > 0)); then
    exit 1
fi
exit 0
