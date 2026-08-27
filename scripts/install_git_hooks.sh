#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PRE_COMMIT_BIN="${PRE_COMMIT_BIN:-$ROOT_DIR/.venv/bin/pre-commit}"
if [[ ! -x "$PRE_COMMIT_BIN" ]]; then
  if command -v pre-commit >/dev/null 2>&1; then
    PRE_COMMIT_BIN="$(command -v pre-commit)"
  else
    echo "[hooks] pre-commit is unavailable; install dev dependencies first:" >&2
    echo "[hooks]   python -m pip install -e '.[dev]'" >&2
    exit 1
  fi
fi

"$PRE_COMMIT_BIN" install --install-hooks --hook-type pre-commit

HOOK_DIR="$(git rev-parse --git-path hooks)"
mkdir -p "$HOOK_DIR"
install -m 0755 "$ROOT_DIR/.githooks/pre-push" "$HOOK_DIR/pre-push"
echo "[hooks] installed pre-commit and pre-push local CI hooks"
