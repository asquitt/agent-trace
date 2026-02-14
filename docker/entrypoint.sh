#!/usr/bin/env sh
set -eu

if [ "${PREFLIGHT_ON_START:-false}" = "true" ]; then
  if ! python -m src.cli.production_preflight; then
    if [ "${PREFLIGHT_STRICT:-true}" = "true" ]; then
      echo "preflight checks failed; refusing to start" >&2
      exit 1
    fi
    echo "preflight checks failed; continuing because PREFLIGHT_STRICT=false" >&2
  fi
fi

if [ "${MIGRATE_ON_START:-true}" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
