#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REPORT_DIR="${SECURITY_REPORT_DIR:-docs/reports/security}"
MAX_KNOWN_VULNS="${SECURITY_MAX_KNOWN_VULNS:-50}"
mkdir -p "$REPORT_DIR"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"

BANDIT_OUT="${REPORT_DIR}/bandit-${TIMESTAMP}.txt"
PIP_AUDIT_OUT="${REPORT_DIR}/pip-audit-${TIMESTAMP}.txt"
PYTEST_OUT="${REPORT_DIR}/security-tests-${TIMESTAMP}.txt"
REPORT_JSON="${REPORT_DIR}/security-gate-${TIMESTAMP}.json"

echo "[security] running bandit"
bandit -q -r src scripts >"$BANDIT_OUT"

echo "[security] running pip-audit"
set +e
pip-audit >"$PIP_AUDIT_OUT" 2>&1
PIP_AUDIT_EXIT=$?
set -e

KNOWN_VULNS="$(grep -Eo 'Found [0-9]+ known vulnerabilities' "$PIP_AUDIT_OUT" | grep -Eo '[0-9]+' | head -n 1 || true)"
KNOWN_VULNS="${KNOWN_VULNS:-0}"

echo "[security] running authz security tests"
pytest -q tests/unit/test_security.py >"$PYTEST_OUT"

STATUS="passed"
if [[ "$KNOWN_VULNS" -gt "$MAX_KNOWN_VULNS" ]]; then
  STATUS="failed"
fi

cat > "$REPORT_JSON" <<JSON
{
  "timestamp_utc": "${TIMESTAMP}",
  "status": "${STATUS}",
  "policy": {
    "max_known_vulns": ${MAX_KNOWN_VULNS}
  },
  "pip_audit": {
    "exit_code": ${PIP_AUDIT_EXIT},
    "known_vulnerabilities": ${KNOWN_VULNS}
  },
  "artifacts": {
    "bandit": "${BANDIT_OUT}",
    "pip_audit": "${PIP_AUDIT_OUT}",
    "pytest_security": "${PYTEST_OUT}"
  }
}
JSON

echo "[security] report written: ${REPORT_JSON}"
cat "$REPORT_JSON"

if [[ "$STATUS" != "passed" ]]; then
  echo "[security] failed: known vulnerabilities (${KNOWN_VULNS}) exceed threshold (${MAX_KNOWN_VULNS})"
  exit 1
fi
