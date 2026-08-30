#!/usr/bin/env bash
set -euo pipefail

# One-command end-to-end validation:
# - fresh pgvector Postgres container
# - migrate to head
# - full test suite
# - migration replay (downgrade/upgrade)
# - full test suite again

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

CONTAINER_NAME="${E2E_DB_CONTAINER:-ai-trace-e2e-pg}"
PORT="${E2E_DB_PORT:-55432}"
DB_NAME="${E2E_DB_NAME:-ai_trace_e2e}"
DB_USER="${E2E_DB_USER:-postgres}"
DB_PASSWORD="${E2E_DB_PASSWORD:-postgres}"
DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@localhost:${PORT}/${DB_NAME}"

ALEMBIC_BIN="${E2E_ALEMBIC_BIN:-./.venv/bin/alembic}"
if [[ ! -x "$ALEMBIC_BIN" ]]; then
  ALEMBIC_BIN="$(command -v alembic)"
fi
PYTEST_BIN="${E2E_PYTEST_BIN:-./.venv/bin/pytest}"
if [[ ! -x "$PYTEST_BIN" ]]; then
  PYTEST_BIN="$(command -v pytest)"
fi

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[e2e] starting fresh database container: ${CONTAINER_NAME}"
cleanup
docker run -d \
  --name "$CONTAINER_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASSWORD" \
  -e POSTGRES_DB="$DB_NAME" \
  -p "${PORT}:5432" \
  pgvector/pgvector:pg16 >/dev/null

echo "[e2e] waiting for database readiness"
for _ in {1..60}; do
  if docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
  echo "[e2e] database did not become ready in time"
  exit 1
fi

echo "[e2e] migration upgrade"
DATABASE_URL="$DATABASE_URL" "$ALEMBIC_BIN" upgrade head

echo "[e2e] test pass #1"
DATABASE_URL="$DATABASE_URL" "$PYTEST_BIN" -q

echo "[e2e] migration replay (downgrade 001 -> upgrade head)"
DATABASE_URL="$DATABASE_URL" "$ALEMBIC_BIN" downgrade 001
DATABASE_URL="$DATABASE_URL" "$ALEMBIC_BIN" upgrade head

echo "[e2e] test pass #2"
DATABASE_URL="$DATABASE_URL" "$PYTEST_BIN" -q

echo "[e2e] success"
