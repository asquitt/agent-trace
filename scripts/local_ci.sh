#!/usr/bin/env bash
set -uo pipefail

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="${LOCAL_CI_ROOT_OVERRIDE:-$SCRIPT_ROOT}"
cd "$ROOT_DIR"

MODE="${1:-}"
if [[ "$MODE" != "--commit" && "$MODE" != "--push" && "$MODE" != "--push-tree" ]]; then
  echo "usage: $0 --commit|--push [ref]|--push-tree <sha>" >&2
  exit 2
fi
if [[ "$MODE" == "--commit" && "$#" -ne 1 ]] || [[ "$MODE" == "--push" && "$#" -gt 2 ]] || [[ "$MODE" == "--push-tree" && "$#" -ne 2 ]]; then
  echo "usage: $0 --commit|--push [ref]|--push-tree <sha>" >&2
  exit 2
fi

TOOLCHAIN_BIN="${LOCAL_CI_TOOLCHAIN_BIN:-$SCRIPT_ROOT/.venv/bin}"
if [[ -d "$TOOLCHAIN_BIN" ]]; then
  export PATH="$TOOLCHAIN_BIN:$PATH"
fi

if [[ "$MODE" == "--push" ]]; then
  TARGET_REF="${2:-HEAD}"
  if ! TARGET_SHA="$(git rev-parse --verify "${TARGET_REF}^{commit}" 2>/dev/null)"; then
    echo "[local-ci] push target is not a commit: $TARGET_REF" >&2
    exit 1
  fi

  TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ai-trace-local-ci.XXXXXX")"
  case "$TEMP_ROOT" in
    */ai-trace-local-ci.*) ;;
    *) echo "[local-ci] unexpected temporary path: $TEMP_ROOT" >&2; exit 1 ;;
  esac
  cleanup_push_tree() {
    find "$TEMP_ROOT" -depth -delete
  }
  trap cleanup_push_tree EXIT

  echo "[local-ci] preparing exact pushed tree: $TARGET_SHA"
  git clone --quiet --no-hardlinks --no-checkout "$ROOT_DIR" "$TEMP_ROOT/repo"
  git -C "$TEMP_ROOT/repo" checkout --quiet --detach "$TARGET_SHA"
  LOCAL_CI_ROOT_OVERRIDE="$TEMP_ROOT/repo" \
    LOCAL_CI_TOOLCHAIN_BIN="$TOOLCHAIN_BIN" \
    bash "$SCRIPT_ROOT/scripts/local_ci.sh" --push-tree "$TARGET_SHA"
  exit $?
fi

if [[ "$MODE" == "--push-tree" ]]; then
  EXPECTED_SHA="$2"
  ACTUAL_SHA="$(git rev-parse HEAD)"
  if [[ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]]; then
    echo "[local-ci] exact-tree mismatch: expected $EXPECTED_SHA, found $ACTUAL_SHA" >&2
    exit 1
  fi
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "[local-ci] exact pushed tree is not clean" >&2
    exit 1
  fi
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
