# Reelay in Docker

The container runs one Telegram polling process as an unprivileged user. It exposes no inbound
ports. SQLite, queued MP4 files, publication checkpoints, rotated TikTok tokens, and the process
lock live in a persistent host directory mounted at `/app/data`.

## First installation

Clone the public source on a Linux host with Docker Engine and Compose v2. From the checkout,
copy `.env.example` to `.env` and configure your accounts using the
[integration guide](../../docs/CROSSPOSTING_SETUP.md). Complete browser-based OAuth locally before
transferring the resulting credentials privately to a headless server.

The default Compose data directory is `./data`. Create it with the container's UID/GID before
starting; Compose deliberately refuses to create a missing mount source.

```bash
sudo install -d -o 10001 -g 10001 -m 0700 ./data
chmod 600 .env
docker compose up --detach --build
docker compose ps
docker compose logs --follow --tail=100 reelay
```

The `.env` file is excluded from the image build context. Compose supplies its contents at runtime.
Use a different environment file or persistent directory as needed:

```bash
REELAY_ENV_FILE=/etc/reelay/reelay.env \
REELAY_HOST_DATA_DIR=/srv/reelay-data \
docker compose up --detach --build
```

The chosen data directory must exist and be writable by UID/GID `10001`. The environment file
must be readable by the operator invoking Compose. Keep the same override values for later
Compose commands, for example by exporting them in the administrative shell.

## Runtime and operations

The image contains Python 3.13, frozen production dependencies from `uv.lock`, FFmpeg/ffprobe
with H.264 and AAC support, and Cloudflare's `cloudflared` binary for `linux/amd64` or
`linux/arm64`. Apple Vision is macOS-only; Linux tagging falls back to caption and source text.

The process runs as UID/GID `10001`, drops Linux capabilities, and uses `no-new-privileges`.
Compose grants up to thirty minutes for an in-flight publication to finish during shutdown.
`restart: unless-stopped` handles process restarts and Docker rotates the local JSON logs.
The health check verifies that the PID recorded in the lock file is alive; it does not check
credentials or make test publications.

```bash
docker compose logs --follow --tail=100 reelay
docker compose restart reelay                       # Restart with the same environment
docker compose up --detach --force-recreate reelay  # Reload configuration
docker compose down
```

`docker compose down` preserves the host data directory and queue. Run exactly one instance
against that directory, on local/block-backed storage rather than NFS or SMB. Do not run a local
bot and a server bot with the same Telegram token at the same time.

## Moving an existing queue

Stop the source instance and keep it stopped during migration. Copy the SQLite database and
`videos` directory together, then ensure the destination data is owned by UID/GID `10001`:

```bash
sudo chown -R 10001:10001 /opt/reelay/data
```

Use that path only if your deployment is actually at `/opt/reelay`; otherwise substitute your
chosen data directory. Keep `reelay.db`, `reelay.db-wal`, and `reelay.db-shm` together for a raw
stopped-service copy, or use SQLite's backup API/command to produce a consistent database.

A moved queue can contain absolute MP4 paths from its previous host. At startup, Reelay rebases
a missing old path to `/app/data/videos/ID/video.mp4` only when that exact job file exists.
Do not copy macOS `data/bin` helpers: their Mach-O binaries cannot run on Linux and the image
already supplies Linux `cloudflared`.

### Private migration backup

**`make server-bundle` deliberately includes `.env`, SQLite, and media in one unencrypted ZIP.
This is a PRIVATE migration backup, never a public release artifact. Do not commit it, attach it
to a GitHub Release, or place it on a public download page.**

For an existing local installation, the helper snapshots SQLite, copies media, checks queue
consistency, and packages the committed source. It requires a clean Git checkout, an installed
Python environment, and the command-line tools listed in `scripts/build-server-bundle.sh`.

```bash
make service-stop
make server-bundle
```

For a foreground or container source, stop that instance instead. The builder also takes the
process lock and refuses to archive active downloads/publications. The generated
`Reelay-All-In-One.zip` has owner-only file permissions; these do not encrypt its contents.
Transfer it only through a private channel to your own deployment host and keep it out of public
release workflows. Use the source repository for distributing code.

The builder prints a SHA-256 checksum. On a Linux server with Docker/Compose, `sha256sum`,
`mktemp`, and `unzip`, verify the transferred file and unpack it in a private directory. Replace
the checksum placeholder with the value printed by the builder:

```bash
umask 077
echo 'PASTE_SHA256_HERE  Reelay-All-In-One.zip' | sha256sum --check
```

Continue only after the checksum succeeds:

```bash
reelay_unpack_dir="$(mktemp -d)"
unzip -q Reelay-All-In-One.zip -d "$reelay_unpack_dir"
sudo bash "$reelay_unpack_dir/reelay-server/deploy/docker/install.sh"
```

The installer defaults to `/opt/reelay`, verifies bundle metadata, assigns storage ownership,
builds the image, and waits for the process health check. It refuses to overwrite an unrelated
non-empty directory or a managed installation at a different source commit. It is a migration
installer, not a general in-place update command. After confirming the server is healthy, remove
the temporary extraction and store or delete the private archive according to your backup needs.
Keep the original bot stopped to avoid competing Telegram polling processes.

## Capacity and connectivity

A practical starting allocation is 2 vCPU and 2 GiB RAM; larger FFmpeg jobs benefit from more RAM.
Size the data volume for your queue and allow additional free space for video derivatives and
temporary Threads transcodes. The [preflight script](../../scripts/server-preflight.sh) can
inspect the host and probe outbound connectivity before deployment.

The bot uses outbound HTTPS and DNS. Threads additionally exposes a temporary video URL through
an outbound Cloudflare Quick Tunnel. TikTok transfers the local file to its returned upload URL.
No inbound firewall port, reverse proxy, or TLS endpoint is required for the bot itself. The
local settings panel is not included as an exposed Docker service.
