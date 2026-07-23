#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: reset-deployment.sh [OPTIONS]

Perform a destructive local deployment reset. This command is intentionally explicit and refuses
to proceed without confirmation and volume removal.

Options:
  --project-name NAME     Compose project name
  --compose-file PATH     Extra compose file (repeatable)
  --confirm               Explicitly confirm intent to destroy deployment state
  --help                  Show this help text
EOF
}

die() {
  echo "mongars-reset: $1" >&2
  exit 1
}

compose_project="${COMPOSE_PROJECT_NAME:-}"
compose_files=()
compose_confirm="false"

while (($# > 0)); do
  case "$1" in
    --project-name)
      if (($# < 2)); then
        die "--project-name requires a value"
      fi
      compose_project=$2
      shift 2
      ;;
    --compose-file)
      if (($# < 2)); then
        die "--compose-file requires a value"
      fi
      compose_files+=("-f" "$2")
      shift 2
      ;;
    --confirm)
      compose_confirm="true"
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

if [[ "${compose_confirm}" != "true" ]]; then
  if [[ -t 0 && -t 1 ]]; then
    confirmation=""
    prompt_label="${compose_project:-$COMPOSE_PROJECT_NAME}"
    if [[ -z "$prompt_label" ]]; then
      prompt_label="mongars"
    fi
    read -r -p "Type RESET-$prompt_label to proceed with destructive reset: " confirmation
    if [[ "$confirmation" != "RESET-$prompt_label" ]]; then
      die "destructive reset cancelled"
    fi
  else
    die "destructive reset requires --confirm in non-interactive mode"
  fi
fi

if ! command -v docker >/dev/null 2>&1; then
  die "docker is required"
fi

compose_command=(docker compose)
if [[ "${#compose_files[@]}" -gt 0 ]]; then
  compose_command+=("${compose_files[@]}")
else
  compose_command+=(-f compose.yaml)
fi
if [[ -n "$compose_project" ]]; then
  compose_command+=(--project-name "$compose_project")
fi

"${compose_command[@]}" down --volumes --remove-orphans --rmi local
