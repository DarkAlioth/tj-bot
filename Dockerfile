FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd -r bot && useradd -r -g bot -d /app bot

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

USER bot

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python", "-c", "import pathlib,sys,time; p = pathlib.Path('/tmp/tj-bot-heartbeat'); sys.exit(0 if p.exists() and time.time() - float(p.read_text()) < 90 else 1)"]

CMD ["tj-bot"]
