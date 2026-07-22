#!/usr/bin/env bash

set -Eeuo pipefail

readonly ci_postgres_image="pgvector/pgvector@sha256:9d2e61c7352b9e9f4798df5fd9a498f043f4cda1cdacc707de3d198650f4321e"
readonly ci_database_name="mongars"
readonly ci_database_user="mongars"
readonly ci_database_password="ci-only-password"

ci_container_id=""
ci_database_probe_only="false"

case "${1:-}" in
  "")
    ;;
  --database-probe-only)
    ci_database_probe_only="true"
    ;;
  *)
    echo "usage: $0 [--database-probe-only]" >&2
    exit 64
    ;;
esac

cleanup_ci_database() {
  ci_exit_status=$?
  trap - EXIT INT TERM

  if [[ -n "$ci_container_id" ]]; then
    if ((ci_exit_status != 0)); then
      echo "ci-local PostgreSQL logs:" >&2
      docker logs "$ci_container_id" >&2 || true
    fi
    docker rm --force "$ci_container_id" >/dev/null 2>&1 || true
  fi

  exit "$ci_exit_status"
}

trap cleanup_ci_database EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ci_script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$ci_script_directory/.."

if command -v uv >/dev/null 2>&1; then
  ci_uv="$(command -v uv)"
elif [[ -x .venv/bin/uv ]]; then
  ci_uv=".venv/bin/uv"
else
  echo "uv is required; install uv 0.11.30 before running make ci-local" >&2
  exit 127
fi

docker info >/dev/null

docker pull "$ci_postgres_image"

if ! ci_container_id="$(
  docker run --detach --rm \
    --label com.mongars.ci-local=true \
    --env "POSTGRES_DB=$ci_database_name" \
    --env "POSTGRES_USER=$ci_database_user" \
    --env "POSTGRES_PASSWORD=$ci_database_password" \
    --publish 127.0.0.1::5432 \
    --health-cmd="pg_isready -U $ci_database_user -d $ci_database_name" \
    --health-interval=2s \
    --health-timeout=3s \
    --health-retries=20 \
    "$ci_postgres_image"
)"; then
  ci_container_id=""
  exit 1
fi
if [[ ! "$ci_container_id" =~ ^[0-9a-f]{12,64}$ ]]; then
  echo "docker run returned an invalid container ID" >&2
  ci_container_id=""
  exit 1
fi

ci_database_health=""
for _ci_attempt in {1..30}; do
  ci_database_health="$(docker inspect --format '{{.State.Health.Status}}' "$ci_container_id")"
  case "$ci_database_health" in
    healthy)
      break
      ;;
    unhealthy)
      echo "ci-local PostgreSQL became unhealthy" >&2
      exit 1
      ;;
  esac
  sleep 1
done

if [[ "$ci_database_health" != "healthy" ]]; then
  echo "ci-local PostgreSQL did not become healthy within 30 seconds" >&2
  exit 1
fi

ci_port_mapping="$(docker port "$ci_container_id" 5432/tcp)"
if [[ ! "$ci_port_mapping" =~ ^127\.0\.0\.1:[0-9]+$ ]]; then
  echo "unexpected ci-local PostgreSQL port mapping: $ci_port_mapping" >&2
  exit 1
fi
ci_postgres_port="${ci_port_mapping##*:}"

export MONGARS_ENVIRONMENT="test"
export MONGARS_DATABASE_URL="postgresql+psycopg://${ci_database_user}:${ci_database_password}@127.0.0.1:${ci_postgres_port}/${ci_database_name}"
export MONGARS_TEST_DATABASE_URL="$MONGARS_DATABASE_URL"
export MONGARS_OLLAMA_BASE_URL="http://127.0.0.1:11434"
export MONGARS_WEB_SEARCH_ENABLED="false"

echo "Running local CI against disposable PostgreSQL on 127.0.0.1:${ci_postgres_port}/${ci_database_name}"

# This narrow diagnostic mode lets the container-ID and lifecycle regression test
# exercise the real startup path without replacing every downstream CI tool.
if [[ "$ci_database_probe_only" == "true" ]]; then
  exit 0
fi

"$ci_uv" lock --check
"$ci_uv" sync --frozen --extra dev --extra documents
shellcheck scripts/*.sh
shellcheck deploy/*/*.sh
"$ci_uv" run ruff format --check .
"$ci_uv" run ruff check .
"$ci_uv" run mypy src
"$ci_uv" run bandit -q -r src
"$ci_uv" run pip-audit
"$ci_uv" run alembic upgrade head
"$ci_uv" run alembic downgrade base
"$ci_uv" run alembic upgrade head
"$ci_uv" run alembic check
"$ci_uv" run pytest -q tests/unit
"$ci_uv" run pytest -q tests/integration
"$ci_uv" run pytest -q tests/unit tests/integration \
  --cov=mongars \
  --cov-branch \
  --cov-report=term-missing \
  --cov-report=xml
docker compose config --quiet
python3 scripts/check_deployment_contract.py
docker build --tag mongars:ci .
docker run --rm --entrypoint python mongars:ci \
  -c 'import os, mongars; assert os.getuid() != 0'
deploy/caddy/check.sh
deploy/egress-proxy/check.sh
deploy/searxng/check.sh
(
  cd apps/mobile
  npm ci
  npm run lint
  npm run typecheck
  npm test
  npm audit --audit-level=high
)
scripts/deployment_smoke.sh
