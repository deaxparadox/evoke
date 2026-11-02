# Build from the repo root: docker compose build backend
# (the context must contain both ragling/ and backend/, because ragling is a path dependency).
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PATH="/app/backend/.venv/bin:$PATH"

WORKDIR /app
COPY ragling ragling
WORKDIR /app/backend
# Dependencies first, so Docker can cache this layer.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --group prod --no-install-project
COPY backend/ .
RUN uv sync --locked --no-dev --group prod

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
