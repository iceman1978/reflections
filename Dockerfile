# The hosted journal (Render builds this; see render.yaml and README → "Hosting on Render").
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

# Entries, backups, logs and settings live on Render's persistent disk.
# (Runs as the container's default user so it can write to the mounted disk.)
ENV JOURNAL_DATA_DIR=/var/data

# One worker process (unlocked sessions are kept in its memory), several threads.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:${PORT:-10000} app:app"]
