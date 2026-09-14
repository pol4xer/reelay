# Development and local operations

## Install and verify

From the repository root:

```bash
make install
make test
make check
```

`make install` synchronizes runtime dependencies and Ruff through `uv`. `make test` runs Python's
`unittest` suite. Tests use temporary data and substituted platform clients; no social account is
required. FFmpeg/ffprobe are needed for the media tests that exercise real transcoding.

`make check` checks the lockfile, Ruff lint and formatting, Python compilation, tests, shell
syntax, and the macOS LaunchAgent template. It also checks browser JavaScript if Node.js is
available and Compose syntax if Docker is available. It does not run a static Python type checker
or live platform publication.

The Python-only checks can also be run explicitly:

```bash
uv lock --check
uv run --no-sync ruff check reelay tests
uv run --no-sync ruff format --check reelay tests
uv run --no-sync python -m unittest discover -s tests -v
```

Use `make format` to apply Ruff fixes and formatting. Review its diff before committing.

## Captions, hashtags, and media

The text after an Instagram URL becomes the caption. Alternatively, the next text message can
be attached to the pending job. Plain text and emoji remain in the caption; hashtags are
deduplicated and rendered on a separate line with `#Reelay` first.

With `AUTO_TAGS=true`, the tagger combines supplied tags, source metadata, and topic rules.
On macOS, Apple Vision adds OCR and visual labels using the bundled Swift helper. Without Vision,
the tagger falls back to textual evidence. `AUTO_TAG_MIN` and `AUTO_TAG_MAX` control the generated
range. TikTok's copyable follow-up is limited to five hashtags by Reelay.

Non-vertical videos are fitted onto a 1080×1920 blurred canvas. With
`VIDEO_WATERMARK_ENABLED=true`, Reelay caches a subtle logo derivative for Instagram, Facebook,
Threads, and YouTube. TikTok receives the original MP4 without the added watermark. A retry reuses
the derivative instead of transcoding it again.

With `ALLOW_PRIVATE_SOURCES=false`, downloads do not read Chrome cookies. Unavailable sources
are skipped with a short Telegram notification. Enabling private-source access uses the configured
local Chrome profile; cookie material belongs outside version control.

When `DELETE_AFTER_PUBLISH=true`, the job's media directory is deleted after all required
deliveries complete. Partially failed jobs retain their files for retry.

## Schedule behavior

`/times 13:00 18:30 21:30` sets 1–12 unique exact slots. SQLite stores this runtime override,
which takes priority over `.env`. `POST_TIMES` supplies initial exact times before a runtime
override is saved. `/times` without arguments shows the active exact schedule.

`/posts 3` clears the exact-time override and distributes three slots evenly between
`POST_WINDOW_START` and `POST_WINDOW_END`. `/posts` without an argument reports the active
schedule. `TIMEZONE` defines the clock used for all slots; `POST_ON_WEEKENDS` controls weekend
publication.

`SCHEDULE_GRACE_MINUTES` bounds late runs. On startup, Reelay considers the latest elapsed slot
and schedules at most one catch-up when the slot is within the grace period and the attempt
history has no run for it. Older missed slots are not drained in a burst.

## Local settings panel

```bash
make config-ui
```

On macOS, double-clicking `Reelay Settings.command` is another entry point. The panel binds to
`127.0.0.1:8765`, validates local requests, and uses a session API token. Credential fields show
whether a value exists; existing secret values are not returned to the browser.

Saving atomically replaces the local `.env`. Schedule and token changes that also have runtime
state are synchronized with SQLite. The restart action targets the macOS LaunchAgent. If a
download or publication is active, the restart is refused and the saved configuration stays
on disk. For Docker, recreate the container to load changed environment values.

The settings panel is intended for local use and is not exposed by the Docker service.

## macOS service

After configuring and testing a foreground run:

```bash
make service-install
make service-status
```

The script installs `~/Library/LaunchAgents/com.pol4xer.reelay.plist`, using the current checkout's
`.venv/bin/python -m reelay`. The LaunchAgent starts after login and uses `KeepAlive` for restart.
Logs are written to `data/logs/reelay.log` and `data/logs/reelay.error.log`. Credentials remain in
the local `.env`, not in the plist.

```bash
make service-start
make service-stop
make service-status
make service-uninstall
```

A lock in `data/reelay.lock` prevents another Reelay process from opening the same queue.
`service-start` also refuses to interrupt a job marked as `publishing`.

The Mac must be awake and logged in for the LaunchAgent to operate. After reboot, the service
starts on login. Sleep suspends local publication until wake; the scheduling grace period
determines whether a missed slot can still run.

## Queue maintenance

```bash
make queue-audit
```

This checks saved platform IDs, expected media files, and ffprobe metadata for pending jobs. It
does not publish a video. Use the Telegram `/queue`, `/status`, and `/retry ID` commands for routine
operation.

If an error says a platform returned an ID that could not be saved, inspect that destination and
repair the recorded ID before retrying. An external API action and a SQLite transaction cannot
be committed atomically together.

## Linux preflight

The standalone preflight script reports operating system, architecture, CPU/RAM/disk, available
tools, Docker/systemd, SQLite WAL behavior, and DNS/HTTPS connectivity. It does not install
software or load credentials. It performs network probes and removes its temporary files.

```bash
bash scripts/server-preflight.sh /opt/reelay/data > reelay-server-report.txt
```

The optional argument must be an absolute future data path; the default is `/opt/reelay/data`.
The report may include host and SSH context, so inspect it before sharing. Deployment and private
queue transfer are documented in [the Docker guide](../deploy/docker/README.md).
