# Reelay in Docker

The production container runs one Telegram polling process as an unprivileged user. It exposes no
inbound ports. SQLite, queued MP4 files, publish checkpoints, and the process lock live in the host
`data` directory bind-mounted at `/app/data`.

## Start

Build and start with the default local `.env` file:

```bash
docker compose up --detach --build
docker compose ps
```

Use a different host-side environment file without copying it into the image:

```bash
REELAY_ENV_FILE=/etc/reelay/reelay.env docker compose up --detach --build
```

The environment file must be readable by the user running Docker and should have mode `0600`.
Compose passes its values to the container at runtime. The file is excluded from the build context.
TikTok OAuth is completed before enabling `PUBLISH_TIKTOK`; the latest rotated TikTok refresh
token is persisted in SQLite because the Compose environment itself is immutable after startup.

Useful operations:

```bash
docker compose logs --follow --tail=100 reelay
docker compose restart reelay  # same environment
docker compose up --detach --force-recreate reelay  # reload .env
docker compose down
```

`docker compose down` keeps the host `data` directory and its queue.

## Runtime contents

The image contains:

- Python 3.13 and frozen production dependencies from `uv.lock`;
- FFmpeg/ffprobe with H.264 and AAC encoders;
- the official Cloudflare `cloudflared` binary for the selected `linux/amd64` or `linux/arm64`
  image platform;
- the Reelay Python package.

The container runs as UID/GID `10001`, drops Linux capabilities, uses `no-new-privileges`, and
receives thirty minutes to finish an in-flight multi-platform publication during shutdown. Docker keeps the process alive with
`restart: unless-stopped`. Logs use the local `json-file` driver with rotation.

## Persistent data and migration

The default host data directory is `./data`. When the Compose project is placed at `/opt/reelay`,
this is `/opt/reelay/data`, matching `scripts/server-preflight.sh`. Override it when necessary:

```bash
REELAY_HOST_DATA_DIR=/srv/reelay-data docker compose up --detach
```

The container runs as UID/GID `10001`, so the host directory must be writable by that identity:

```bash
sudo install -d -o 10001 -g 10001 -m 0700 /opt/reelay/data
```

After copying an existing SQLite database and videos, apply ownership again:

```bash
sudo chown -R 10001:10001 /opt/reelay/data
```

Compose deliberately refuses to create a missing host data directory, preventing a root-owned
empty directory from sending the container into a restart loop.

Do not copy the macOS `data/bin` directory into the volume. Its Mach-O `cloudflared` and Apple
Vision helper cannot run on Linux; the image already supplies the correct Linux `cloudflared`.

An existing macOS queue stores absolute MP4 paths such as
`/path/to/reelay/data/videos/ID/video.mp4`. Before starting the migrated container, copy the
SQLite database and `videos` directory while the local Reelay service is stopped. On startup Reelay
rebases a missing old path to `/app/data/videos/ID/video.mp4` when that exact job file exists. Keep
`reelay.db`, `reelay.db-wal`, and `reelay.db-shm` together during a raw stopped-service copy, or use
SQLite's backup command.

Run exactly one container against the data directory. Telegram long polling, the process lock, and SQLite
WAL are intentionally single-instance. Use a local/block-backed Docker volume, not NFS or SMB.

## Resource baseline

Use at least 2 vCPU and 2 GiB RAM; 4 GiB RAM is preferable for 1080×1920 FFmpeg transcodes.
Allocate 10–20 GiB to the data volume and leave at least 2 GiB free for the container's temporary
Threads MP4. Outbound HTTPS/DNS access is required, including `www.tiktok.com`,
`open.tiktokapis.com` and the returned `open-upload.tiktokapis.com` upload URL; no inbound firewall
rule is needed.
