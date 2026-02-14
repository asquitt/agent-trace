#!/usr/bin/env sh
set -eu

if [ "${MIGRATE_ON_START:-true}" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
