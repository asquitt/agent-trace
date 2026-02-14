#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONTAINER_NAME="${DRILL_DB_CONTAINER:-ai-trace-drill-pg}"
PORT="${DRILL_DB_PORT:-56432}"
DB_NAME="${DRILL_DB_NAME:-ai_trace_drill}"
DB_USER="${DRILL_DB_USER:-postgres}"
DB_PASSWORD="${DRILL_DB_PASSWORD:-postgres}"
DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@localhost:${PORT}/${DB_NAME}"
REPORT_DIR="${DRILL_REPORT_DIR:-docs/reports/dr}"

mkdir -p "$REPORT_DIR"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
BACKUP_FILE="${REPORT_DIR}/backup-${TIMESTAMP}.dump"
REPORT_FILE="${REPORT_DIR}/backup-restore-drill-${TIMESTAMP}.json"

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[drill] starting isolated postgres container: ${CONTAINER_NAME}"
cleanup
docker run -d \
  --name "$CONTAINER_NAME" \
  -e POSTGRES_USER="$DB_USER" \
  -e POSTGRES_PASSWORD="$DB_PASSWORD" \
  -e POSTGRES_DB="$DB_NAME" \
  -p "${PORT}:5432" \
  pgvector/pgvector:pg16 >/dev/null

echo "[drill] waiting for database readiness"
for _ in {1..60}; do
  if docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
  echo "[drill] database readiness check failed"
  exit 1
fi

ALEMBIC_BIN="./.venv/bin/alembic"
if [[ ! -x "$ALEMBIC_BIN" ]]; then
  ALEMBIC_BIN="alembic"
fi

echo "[drill] applying migrations"
DATABASE_URL="$DATABASE_URL" "$ALEMBIC_BIN" upgrade head >/dev/null

DEPLOYMENT_ID="11111111-1111-1111-1111-111111111111"
SESSION_ID="22222222-2222-2222-2222-222222222222"
ACTION_ID="33333333-3333-3333-3333-333333333333"

echo "[drill] seeding drill data"
docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL >/dev/null
INSERT INTO agent_deployments (id, org_id, deployment_key, name, environment, runtime, runtime_version, is_active, metadata)
VALUES ('${DEPLOYMENT_ID}', 'drill-org', 'drill-key', 'DR Drill Deployment', 'prod', 'drill-runtime', '1.0.0', true, '{}'::jsonb);

INSERT INTO agent_sessions (id, deployment_id, agent_id, status, started_at, tags, metadata)
VALUES ('${SESSION_ID}', '${DEPLOYMENT_ID}', 'drill-agent', 'active', NOW(), '[]'::jsonb, '{}'::jsonb);

INSERT INTO agent_actions (
  id, session_id, action_type, action_name, input_tokens, output_tokens, estimated_cost_usd,
  success, occurred_at, metadata
)
VALUES (
  '${ACTION_ID}', '${SESSION_ID}', 'tool_call', 'drill-action', 10, 4, 0.0001,
  true, NOW(), '{}'::jsonb
);
SQL

count_query() {
  local table_name="$1"
  docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -tAc "SELECT COUNT(*) FROM ${table_name};" | tr -d '[:space:]'
}

BEFORE_DEPLOYMENTS="$(count_query agent_deployments)"
BEFORE_SESSIONS="$(count_query agent_sessions)"
BEFORE_ACTIONS="$(count_query agent_actions)"

echo "[drill] creating compressed backup: ${BACKUP_FILE}"
docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$BACKUP_FILE"

echo "[drill] simulating disaster (drop schema)"
docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null

echo "[drill] restoring backup"
cat "$BACKUP_FILE" | docker exec -i "$CONTAINER_NAME" pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --no-privileges >/dev/null

AFTER_DEPLOYMENTS="$(count_query agent_deployments)"
AFTER_SESSIONS="$(count_query agent_sessions)"
AFTER_ACTIONS="$(count_query agent_actions)"

PASSED="true"
if [[ "$BEFORE_DEPLOYMENTS" != "$AFTER_DEPLOYMENTS" || "$BEFORE_SESSIONS" != "$AFTER_SESSIONS" || "$BEFORE_ACTIONS" != "$AFTER_ACTIONS" ]]; then
  PASSED="false"
fi

BACKUP_BYTES="$(wc -c < "$BACKUP_FILE" | tr -d '[:space:]')"

cat > "$REPORT_FILE" <<JSON
{
  "timestamp_utc": "${TIMESTAMP}",
  "status": "${PASSED}",
  "database_url": "${DATABASE_URL}",
  "backup_file": "${BACKUP_FILE}",
  "backup_size_bytes": ${BACKUP_BYTES},
  "before": {
    "agent_deployments": ${BEFORE_DEPLOYMENTS},
    "agent_sessions": ${BEFORE_SESSIONS},
    "agent_actions": ${BEFORE_ACTIONS}
  },
  "after": {
    "agent_deployments": ${AFTER_DEPLOYMENTS},
    "agent_sessions": ${AFTER_SESSIONS},
    "agent_actions": ${AFTER_ACTIONS}
  }
}
JSON

echo "[drill] report written: ${REPORT_FILE}"
cat "$REPORT_FILE"

if [[ "$PASSED" != "true" ]]; then
  echo "[drill] backup/restore validation failed"
  exit 1
fi

echo "[drill] success"
