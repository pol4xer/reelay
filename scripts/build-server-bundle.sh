#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
OUTPUT_PATH="${1:-${PROJECT_ROOT}/Reelay-All-In-One.zip}"
SOURCE_DB="${PROJECT_ROOT}/data/reelay.db"
SOURCE_ENV="${PROJECT_ROOT}/.env"
TEMP_ROOT=""

cleanup() {
    case "${TEMP_ROOT:-}" in
        /tmp/reelay-server-bundle.*)
            [[ -d "${TEMP_ROOT}" ]] && rm -rf -- "${TEMP_ROOT}"
            ;;
    esac
}
trap cleanup EXIT INT TERM

die() {
    echo "Bundle build failed: $*" >&2
    exit 1
}

if [[ "${OUTPUT_PATH}" != /* ]]; then
    OUTPUT_PATH="$(cd "$(dirname "${OUTPUT_PATH}")" && pwd)/$(basename "${OUTPUT_PATH}")"
fi

for command_name in git sqlite3 rsync zip unzip shasum; do
    command -v "${command_name}" >/dev/null 2>&1 || die "missing command: ${command_name}"
done
[[ -x "${PROJECT_ROOT}/.venv/bin/python" ]] || die "project Python environment is missing"
[[ -f "${SOURCE_DB}" ]] || die "SQLite database is missing"
[[ -f "${SOURCE_ENV}" ]] || die ".env is missing"
[[ -z "$(git -C "${PROJECT_ROOT}" status --porcelain)" ]] || die "git worktree must be clean"

if [[ "${REELAY_BUNDLE_LOCK_HELD:-}" != "1" ]]; then
    exec "${PROJECT_ROOT}/.venv/bin/python" - \
        "${PROJECT_ROOT}/data/reelay.lock" "$0" "$@" <<'PY'
import fcntl
import os
import sys

lock_path, script, *arguments = sys.argv[1:]
lock_file = open(lock_path, "a+", encoding="utf-8")
try:
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(
        "Bundle build failed: stop the local Reelay service before building the bundle"
    ) from None

lock_file.seek(0)
lock_file.truncate()
lock_file.write(str(os.getpid()))
lock_file.flush()
os.set_inheritable(lock_file.fileno(), True)
environment = os.environ.copy()
environment["REELAY_BUNDLE_LOCK_HELD"] = "1"
environment["REELAY_BUNDLE_LOCK_FD"] = str(lock_file.fileno())
os.execve("/bin/bash", ["bash", script, *arguments], environment)
PY
fi

source_env_hash="$(shasum -a 256 "${SOURCE_ENV}" | awk '{print $1}')"

active_jobs="$(
    sqlite3 "${SOURCE_DB}" \
        "SELECT COUNT(*) FROM jobs WHERE status IN ('downloading', 'publishing');"
)"
[[ "${active_jobs}" == "0" ]] || die "a download or publication is currently active"

TEMP_ROOT="$(mktemp -d /tmp/reelay-server-bundle.XXXXXX)"
PAYLOAD_ROOT="${TEMP_ROOT}/reelay-server"
SNAPSHOT_DB="${TEMP_ROOT}/snapshot.db"
VERIFY_DB="${TEMP_ROOT}/verify.db"
TEMP_OUTPUT="${TEMP_ROOT}/Reelay-All-In-One.zip"
mkdir -p "${PAYLOAD_ROOT}/data/videos"

git -C "${PROJECT_ROOT}" archive --format=tar HEAD | tar -xf - -C "${PAYLOAD_ROOT}"
cp -p "${SOURCE_ENV}" "${PAYLOAD_ROOT}/.env"
chmod 0600 "${PAYLOAD_ROOT}/.env"

sqlite3 "${SOURCE_DB}" ".backup '${SNAPSHOT_DB}'"
[[ "$(sqlite3 "${SNAPSHOT_DB}" 'PRAGMA quick_check;')" == "ok" ]] \
    || die "SQLite snapshot failed quick_check"
cp -p "${SNAPSHOT_DB}" "${PAYLOAD_ROOT}/data/reelay.db"

rsync -a \
    --exclude '.DS_Store' \
    --exclude '.tag_frames' \
    "${PROJECT_ROOT}/data/videos/" \
    "${PAYLOAD_ROOT}/data/videos/"

PYTHONDONTWRITEBYTECODE=1 "${PROJECT_ROOT}/.venv/bin/python" - "${PAYLOAD_ROOT}" <<'PY'
import json
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1])
connection = sqlite3.connect(root / "data" / "reelay.db")
connection.row_factory = sqlite3.Row
if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
    raise SystemExit("snapshot quick_check failed")

checked = 0
for job in connection.execute(
    "SELECT id, shortcode, status, video_path FROM jobs "
    "WHERE status IN ('queued', 'failed', 'publishing', 'downloading')"
):
    if not job["video_path"]:
        continue
    video = root / "data" / "videos" / str(job["id"]) / "video.mp4"
    info = video.with_name("video.info.json")
    if not video.is_file() or not info.is_file():
        raise SystemExit(f"missing media for job #{job['id']}")
    source_id = str(json.loads(info.read_text(encoding="utf-8"))["id"])
    if source_id != job["shortcode"]:
        raise SystemExit(f"metadata mismatch for job #{job['id']}")
    checked += 1

print(f"Verified queued media: {checked}")
PY

cat >"${PAYLOAD_ROOT}/BUNDLE_INFO.txt" <<EOF
Reelay all-in-one server bundle
Created UTC: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
Git commit: $(git -C "${PROJECT_ROOT}" rev-parse HEAD)
Jobs: $(sqlite3 "${SNAPSHOT_DB}" 'SELECT COUNT(*) FROM jobs;')
Queued: $(sqlite3 "${SNAPSHOT_DB}" "SELECT COUNT(*) FROM jobs WHERE status='queued';")
EOF

(
    cd "${TEMP_ROOT}"
    zip -qry "${TEMP_OUTPUT}" reelay-server
)
unzip -tq "${TEMP_OUTPUT}" >/dev/null

sqlite3 "${SOURCE_DB}" ".backup '${VERIFY_DB}'"
snapshot_hash="$(sqlite3 "${SNAPSHOT_DB}" .dump | shasum -a 256 | awk '{print $1}')"
verify_hash="$(sqlite3 "${VERIFY_DB}" .dump | shasum -a 256 | awk '{print $1}')"
[[ "${snapshot_hash}" == "${verify_hash}" ]] \
    || die "queue changed while the bundle was being created; run again"
[[ "${source_env_hash}" == "$(shasum -a 256 "${SOURCE_ENV}" | awk '{print $1}')" ]] \
    || die ".env changed while the bundle was being created; run again"

active_jobs="$(
    sqlite3 "${SOURCE_DB}" \
        "SELECT COUNT(*) FROM jobs WHERE status IN ('downloading', 'publishing');"
)"
[[ "${active_jobs}" == "0" ]] || die "queue became active while building the bundle"

chmod 0600 "${TEMP_OUTPUT}"
mkdir -p "$(dirname "${OUTPUT_PATH}")"
mv -f "${TEMP_OUTPUT}" "${OUTPUT_PATH}"
chmod 0600 "${OUTPUT_PATH}"

echo "Created: ${OUTPUT_PATH}"
echo "SHA-256: $(shasum -a 256 "${OUTPUT_PATH}" | awk '{print $1}')"
echo "Contains credentials and queue data; send it only to the server owner."
echo "Keep the local Reelay instance stopped until the server container is healthy."
