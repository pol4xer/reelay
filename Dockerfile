# syntax=docker/dockerfile:1.7

ARG UV_VERSION=0.9.28
ARG CLOUDFLARED_IMAGE=cloudflare/cloudflared:2026.8.2@sha256:0aa26e284f05e6c77ae375b8c9c11d9eb6a448fb7bcd8d40f31cb6176189eb38

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM ${CLOUDFLARED_IMAGE} AS cloudflared
FROM python:3.13-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/app/.venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        ffmpeg \
        tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 reelay \
    && useradd \
        --uid 10001 \
        --gid 10001 \
        --create-home \
        --shell /usr/sbin/nologin \
        reelay

COPY --from=uv /uv /usr/local/bin/uv
COPY --from=cloudflared /usr/local/bin/cloudflared /usr/local/bin/cloudflared

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && ffmpeg -hide_banner -encoders 2>/dev/null | grep -q 'libx264' \
    && ffmpeg -hide_banner -encoders 2>/dev/null | grep -q ' aac ' \
    && cloudflared --version

COPY --chown=10001:10001 reelay ./reelay
COPY --chown=10001:10001 assets/reelay-telegram-avatar.png ./assets/reelay-telegram-avatar.png

RUN mkdir -p /app/data \
    && chown 10001:10001 /app/data

USER 10001:10001

VOLUME ["/app/data"]

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import os, pathlib; pid = int(pathlib.Path('/app/data/reelay.lock').read_text().strip()); os.kill(pid, 0)"]

CMD ["/app/.venv/bin/python", "-m", "reelay"]
