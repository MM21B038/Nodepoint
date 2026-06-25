FROM python:3.14-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=config.settings \
    UV_SYSTEM_PYTHON=1 \
    UV_NO_DEV=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

RUN SECRET_KEY=build-only /app/.venv/bin/python manage.py collectstatic --noinput

RUN chmod +x docker/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/bin/sh", "/app/docker/entrypoint.sh"]
CMD ["/bin/sh", "-c", "exec /app/.venv/bin/uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers ${WEB_WORKERS:-4}"]
