#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: mongars-status.sh [OPTIONS]

Collect a bounded operational status snapshot from a local monGARS deployment.

Options:
  --api-url URL           Base API URL (default: MONGARS_STATUS_API_URL or MONGARS_ORIGIN or http://127.0.0.1:8000)
  --token-file PATH       API token file (default: MONGARS_STATUS_API_TOKEN_FILE or MONGARS_API_TOKEN_FILE)
  --compose-project NAME  Compose project name for status checks
  --json                  Emit machine-readable JSON output
  --help                  Show this help text
EOF
}

die() {
  local message=$1
  echo "mongars-status: $message" >&2
  exit 1
}

api_url="${MONGARS_STATUS_API_URL:-${MONGARS_ORIGIN:-http://127.0.0.1:8000}}"
token="${MONGARS_STATUS_API_TOKEN:-${MONGARS_API_TOKEN:-}}"
token_file="${MONGARS_STATUS_API_TOKEN_FILE:-${MONGARS_API_TOKEN_FILE:-}}"
compose_project="${COMPOSE_PROJECT_NAME:-}"
json_mode="false"

while (($# > 0)); do
  case "$1" in
    --api-url)
      if (($# < 2)); then
        die "--api-url requires a value"
      fi
      api_url=$2
      shift 2
      ;;
    --token-file)
      if (($# < 2)); then
        die "--token-file requires a value"
      fi
      token_file=$2
      shift 2
      ;;
    --compose-project)
      if (($# < 2)); then
        die "--compose-project requires a value"
      fi
      compose_project=$2
      shift 2
      ;;
    --json)
      json_mode="true"
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

if [[ -z "$token" && -n "$token_file" ]]; then
  if [[ ! -f "$token_file" ]]; then
    die "token file not found: $token_file"
  fi
  token="$(< "$token_file")"
fi
if [[ -z "$token" ]]; then
  die "set MONGARS_STATUS_API_TOKEN or provide --token-file"
fi
token="${token%$'\r'}"

if [[ ! "$token" ]]; then
  die "token file is empty"
fi

if [[ ! "$api_url" =~ ^https?:// ]]; then
  die "api URL must be absolute"
fi

compose_command=(docker compose)
if [[ -n "$compose_project" ]]; then
  compose_command+=(--project-name "$compose_project")
fi

readyz_tmp="$(mktemp)"
tasks_tmp="$(mktemp)"
readonly readyz_tmp tasks_tmp

cleanup() {
  rm -f "$readyz_tmp" "$tasks_tmp"
}
trap cleanup EXIT

curl_request() {
  local url=$1
  local output_file=$2
  curl --silent --show-error --output "$output_file" \
    --write-out '%{http_code}' \
    --connect-timeout 5 \
    --max-time 20 \
    --header "Authorization: Bearer $token" \
    "$url"
}

compose_services=$(
  if command -v docker >/dev/null; then
    "${compose_command[@]}" ps --format '{{.Name}}\t{{.State}}\t{{.Health}}\t{{.Status}}' 2>/dev/null || true
  else
    echo "docker-unavailable\tnot_available\t\t"
  fi
)

compose_disk_pressure=$(
  if command -v docker >/dev/null; then
    docker system df || true
  else
    echo "docker system df unavailable"
  fi
)

readyz_http=$(curl_request "$api_url/v1/readyz" "$readyz_tmp")
tasks_http=$(curl_request "$api_url/v1/tasks?limit=100" "$tasks_tmp")

python3 - "$readyz_tmp" "$tasks_tmp" "$readyz_http" "$tasks_http" "$api_url" "$compose_project" "$compose_services" "$compose_disk_pressure" "$json_mode" <<'PY'
from __future__ import annotations

import json
import sys

readyz_path, tasks_path, readyz_http, tasks_http, api_url, compose_project, compose_services, disk_pressure, json_mode = sys.argv[1:]


def _load_json(path: str) -> object:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _bool(value: str) -> bool:
    return value.lower() in {"true", "1", "yes", "on"}


def _parse_compose_services(raw: str) -> list[dict[str, str | None]]:
    services = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[0]
        state = parts[1]
        health = parts[2] if len(parts) >= 3 else None
        status = parts[3] if len(parts) >= 4 else None
        services.append({"name": name, "state": state, "health": health or None, "status": status})
    return services


errors = []
if readyz_http != "200" and readyz_http != "503":
    errors.append(f"readyz HTTP {readyz_http}")
if tasks_http != "200":
    errors.append(f"tasks HTTP {tasks_http}")

readyz_obj: dict[str, object] | None = None
readyz_dependencies: dict[str, object] | None = None
if readyz_http in {"200", "503"}:
    try:
        readyz_obj = _load_json(readyz_path)
        readyz_dependencies = readyz_obj.get("dependencies", {}) if isinstance(readyz_obj, dict) else None
        if not isinstance(readyz_dependencies, dict):
            raise ValueError("readyz payload missing dependencies")
    except Exception as exc:
        errors.append(f"failed to parse readyz JSON: {exc}")
        readyz_obj = None
        readyz_dependencies = None

pending_approvals = None
tasks_count = 0
if tasks_http == "200":
    try:
        tasks = _load_json(tasks_path)
        if not isinstance(tasks, list):
            raise ValueError("tasks payload is not a list")
        tasks_count = len(tasks)
        pending_approvals = sum(
            1
            for task in tasks
            if isinstance(task, dict) and task.get("status") == "waiting_approval"
        )
    except Exception as exc:
        errors.append(f"failed to parse tasks JSON: {exc}")

dependencies = {
    "database": readyz_dependencies.get("database") if isinstance(readyz_dependencies, dict) else None,
    "inference": readyz_dependencies.get("inference") if isinstance(readyz_dependencies, dict) else None,
    "parser": readyz_dependencies.get("parser") if isinstance(readyz_dependencies, dict) else None,
    "worker": readyz_dependencies.get("worker") if isinstance(readyz_dependencies, dict) else None,
    "embedding_space": readyz_dependencies.get("embedding_space") if isinstance(readyz_dependencies, dict) else None,
}

payload = {
    "api_url": api_url,
    "compose_project": compose_project,
    "http_status": {
        "readyz": int(readyz_http),
        "tasks": int(tasks_http),
    },
    "status": readyz_obj.get("status") if isinstance(readyz_obj, dict) else None,
    "dependencies": {
        "ready": bool(readyz_obj is not None and readyz_obj.get("status") == "ready") if isinstance(readyz_obj, dict) else False,
        "database": dependencies["database"],
        "inference": dependencies["inference"],
        "parser": dependencies["parser"],
        "worker": dependencies["worker"],
        "embedding": dependencies["embedding_space"],
    },
    "pending_approvals": {
        "count": pending_approvals,
        "sampled_tasks": tasks_count,
        "sampled_limit": 100,
        "truncated": pending_approvals is not None and tasks_count >= 100,
    },
    "compose": {
        "project": compose_project,
        "services": _parse_compose_services(compose_services),
        "disk_pressure": disk_pressure,
    },
    "errors": errors,
}

if not _bool(sys.argv[-1]):
    print(f"api: {api_url}")
    if readyz_obj is not None and isinstance(readyz_obj, dict):
        print(f"readyz: {readyz_obj.get('status')} (http={readyz_http})")
    else:
        print(f"readyz: unavailable (http={readyz_http})")

    inference = dependencies["inference"] if isinstance(dependencies["inference"], dict) else None
    worker = dependencies["worker"] if isinstance(dependencies["worker"], dict) else None
    parser = dependencies["parser"] if isinstance(dependencies["parser"], dict) else None
    embedding = dependencies["embedding"] if isinstance(dependencies["embedding"], dict) else None

    if inference is not None:
        print(
            "inference: backend={backend} model_ready={chat}/{emb}".format(
                backend=inference.get("backend"),
                chat=inference.get("chat_model_ready"),
                emb=inference.get("embedding_model_ready"),
            )
        )
    if parser is not None:
        print(f"parser: healthy={parser.get('healthy')} version={parser.get('version')}")
    if worker is not None:
        print(
            "worker: status={status} age_seconds={age_seconds} instance_id={instance_id}".format(
                status=worker.get("status"),
                age_seconds=worker.get("age_seconds"),
                instance_id=worker.get("instance_id"),
            )
        )
    if embedding is not None:
        print(
            "embedding: space_id={space_id} model_alias={model_alias} digest={model_digest} reindex_required={reindex_required}".format(
                space_id=embedding.get("space_id"),
                model_alias=embedding.get("model_alias"),
                model_digest=embedding.get("model_digest"),
                reindex_required=embedding.get("reindex_required"),
            )
        )
    if pending_approvals is not None:
        print(
            "pending approvals: {count} (sampled {sampled}/{limit})".format(
                count=pending_approvals,
                sampled=tasks_count,
                limit=100,
            )
        )
    if errors:
        print("errors:")
        for error in errors:
            print(f"  - {error}")
    else:
        print("errors: none")
    if compose_services:
        print("compose services:")
        for service in _parse_compose_services(compose_services):
            print(
                "  - {name}: state={state} health={health} status={status}".format(
                    name=service["name"],
                    state=service["state"],
                    health=service["health"],
                    status=service["status"],
                )
            )
    print("disk pressure:")
    if isinstance(disk_pressure, str):
        for line in disk_pressure.splitlines()[:20]:
            print(f"  {line}")
    exit_code = 0
    if readyz_http == "503":
        exit_code = 2
    print()
else:
    print(json.dumps(payload, sort_keys=True))
    exit_code = 0

sys.exit(exit_code)
PY

status=$?
if [[ "$json_mode" == "true" ]]; then
  exit "$status"
fi
if [[ "$readyz_http" == "200" ]]; then
  exit 0
fi
if [[ "$readyz_http" == "503" ]]; then
  exit 2
fi
if [[ "$readyz_http" == "000" ]]; then
  exit 1
fi
exit "$status"
