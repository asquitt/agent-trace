#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

API_PORT="${PERF_GATE_API_PORT:-8010}"
BASE_URL="http://127.0.0.1:${API_PORT}"
DATABASE_URL="${PERF_GATE_DATABASE_URL:-postgresql+asyncpg://postgres:postgres@localhost:5434/ai_trace}"

echo "[perf] starting dependencies (db/redis)"
(
  cd docker
  docker compose up -d db redis >/dev/null
)

echo "[perf] waiting for database readiness"
for _ in {1..60}; do
  if docker exec ai-trace-db pg_isready -U postgres -d ai_trace >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker exec ai-trace-db pg_isready -U postgres -d ai_trace >/dev/null 2>&1; then
  echo "[perf] database readiness check failed"
  exit 1
fi

echo "[perf] applying migrations"
ALEMBIC_BIN="./.venv/bin/alembic"
if [[ ! -x "$ALEMBIC_BIN" ]]; then
  ALEMBIC_BIN="alembic"
fi
DATABASE_URL="$DATABASE_URL" "$ALEMBIC_BIN" upgrade head >/dev/null

UVICORN_BIN="./.venv/bin/uvicorn"
if [[ ! -x "$UVICORN_BIN" ]]; then
  UVICORN_BIN="uvicorn"
fi

echo "[perf] starting API on ${BASE_URL}"
DATABASE_URL="$DATABASE_URL" "$UVICORN_BIN" src.api.main:app --host 127.0.0.1 --port "$API_PORT" >/tmp/ai-trace-perf-uvicorn.log 2>&1 &
UVICORN_PID="$!"

cleanup() {
  if kill -0 "$UVICORN_PID" >/dev/null 2>&1; then
    kill "$UVICORN_PID" >/dev/null 2>&1 || true
    wait "$UVICORN_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "[perf] waiting for readiness"
for _ in {1..60}; do
  if curl -fsS "${BASE_URL}/health/ready" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "${BASE_URL}/health/ready" >/dev/null 2>&1; then
  echo "[perf] API readiness check failed"
  exit 1
fi

echo "[perf] running performance gate"
PYTHON_BIN="./.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python"
fi
"$PYTHON_BIN" scripts/perf_gate.py --base-url "$BASE_URL"

echo "[perf] complete"
