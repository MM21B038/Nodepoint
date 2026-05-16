#!/bin/sh
set -e

PG_HOST="${POSTGRES_HOST:-postgres}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:-nodepoint}"

echo "Waiting for PostgreSQL at ${PG_HOST}:${PG_PORT}..."
until pg_isready -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" > /dev/null 2>&1; do
  sleep 1
done
echo "PostgreSQL is ready."

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  /app/.venv/bin/python manage.py migrate --noinput
fi

exec "$@"
