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
# collectstatic imports settings, which need the required values. Throwaway values for this one command only; not kept in the image.
RUN SECRET_KEY=build-only ALLOWED_HOSTS=localhost POSTGRES_HOST=none POSTGRES_PORT=0 POSTGRES_DB=none POSTGRES_USER=none POSTGRES_PASSWORD=none \
    S3_ENDPOINT=none S3_ACCESS_KEY=none S3_SECRET_KEY=none S3_BUCKET=none \
    python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
