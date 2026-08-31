#!/usr/bin/env sh
set -eu

parse_boolean() {
  variable_name=$1
  default_value=$2
  is_set=$3
  raw_value=$4

  if [ "$is_set" != "x" ]; then
    printf '%s\n' "$default_value"
    return 0
  fi

  normalized_value=$(
    printf '%s' "$raw_value" |
      LC_ALL=C sed 's/^[[:space:]]*//; s/[[:space:]]*$//' |
      LC_ALL=C tr '[:upper:]' '[:lower:]'
  )

  case "$normalized_value" in
    true | 1 | yes | on)
      printf '%s\n' "true"
      ;;
    false | 0 | no | off)
      printf '%s\n' "false"
      ;;
    *)
      echo "$variable_name must be one of: true, false, 1, 0, yes, no, on, off" >&2
      return 2
      ;;
  esac
}

preflight_on_start=$(
  parse_boolean "PREFLIGHT_ON_START" "false" "${PREFLIGHT_ON_START+x}" "${PREFLIGHT_ON_START-}"
) || exit 2
preflight_strict=$(
  parse_boolean "PREFLIGHT_STRICT" "true" "${PREFLIGHT_STRICT+x}" "${PREFLIGHT_STRICT-}"
) || exit 2
migrate_on_start=$(
  parse_boolean "MIGRATE_ON_START" "false" "${MIGRATE_ON_START+x}" "${MIGRATE_ON_START-}"
) || exit 2

if [ "$preflight_on_start" = "true" ]; then
  if ! python -m src.cli.production_preflight; then
    if [ "$preflight_strict" = "true" ]; then
      echo "preflight checks failed; refusing to start" >&2
      exit 1
    fi
    echo "preflight checks failed; continuing because PREFLIGHT_STRICT=false" >&2
  fi
fi

if [ "$migrate_on_start" = "true" ]; then
  alembic upgrade head
fi

exec "$@"
