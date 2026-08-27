#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-}"
if [[ "$MODE" != "--commit" && "$MODE" != "--push" ]]; then
  echo "usage: $0 --commit|--push" >&2
  exit 2
fi

if [[ -d "$ROOT_DIR/.venv/bin" ]]; then
  export PATH="$ROOT_DIR/.venv/bin:$PATH"
fi

PASS_COUNT=0
FAIL_COUNT=0
FAILED_GATES=()

pass() {
  printf '  PASS  %s\n' "$1"
  PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
  printf '  FAIL  %s\n' "$1"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_GATES+=("$1")
}

gate() {
  local name="$1"
  shift
  local output
  local result

  output="$("$@" 2>&1)"
  result=$?
  if [[ "$result" -eq 0 ]]; then
    pass "$name"
    return 0
  fi

  fail "$name"
  printf '%s\n' "$output" | tail -40 | sed 's/^/        /'
  return 1
}

require_command() {
  local command_name="$1"
  if command -v "$command_name" >/dev/null 2>&1; then
    pass "tool available: $command_name"
    return 0
  fi

  fail "tool available: $command_name"
  return 1
}

finish() {
  printf '\nResult: %d passed, %d failed\n' "$PASS_COUNT" "$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -gt 0 ]]; then
    printf 'Failed gates:\n'
    printf '  - %s\n' "${FAILED_GATES[@]}"
    exit 1
  fi
  exit 0
}

echo "Local CI"
echo "  mode: ${MODE#--}"
echo "  head: $(git rev-parse HEAD)"
echo

if [[ "$MODE" == "--commit" ]]; then
  gate "staged diff integrity" git diff --cached --check || true
  require_command ruff || true
  if command -v ruff >/dev/null 2>&1; then
    gate "Python undefined-name/import lint" ruff check --select F src tests || true
  fi
  finish
fi

require_command ruff || true
require_command pyright || true
require_command pytest || true
require_command alembic || true
require_command bandit || true
require_command pip-audit || true
require_command npm || true
require_command docker || true

if [[ "$FAIL_COUNT" -gt 0 ]]; then
  finish
fi

gate "working-tree diff integrity" git diff --check || true
gate "Python undefined-name/import lint" ruff check --select F src tests || true
gate "Python type check" pyright || true
gate "operator-console clean dependency install" npm --prefix web ci --no-audit --no-fund || true
gate "operator-console tests" npm --prefix web test || true
gate "operator-console type check" npm --prefix web run typecheck || true
gate "operator-console production build" npm --prefix web run build || true
gate "PostgreSQL tests and migration replay" ./scripts/run_full_e2e.sh || true
gate "locked-dependency security gate" ./scripts/security_gate.sh || true
gate "production Docker build" docker build -f docker/Dockerfile . || true

finish
