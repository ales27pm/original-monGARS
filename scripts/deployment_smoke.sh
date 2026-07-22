#!/usr/bin/env bash

set -Eeuo pipefail

script_directory="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "$script_directory/.." && pwd)"
project_name="mongars-ci-smoke-$$"
temporary_directory="$(mktemp -d /tmp/mongars-ci-smoke.XXXXXXXX)"
compose_files=(-f compose.yaml -f compose.ci-smoke.yaml)

published_loopback_port() {
  target_port="$1"
  port_mapping="$(
    docker compose "${compose_files[@]}" --project-name "$project_name" \
      port https "$target_port"
  )"
  if [[ ! "$port_mapping" =~ ^127\.0\.0\.1:([0-9]+)$ ]]; then
    echo "unexpected HTTPS service port mapping for $target_port: $port_mapping" >&2
    return 1
  fi
  printf '%s\n' "${BASH_REMATCH[1]}"
}

cleanup() {
  exit_status=$?
  trap - EXIT INT TERM
  if ((exit_status != 0)); then
    docker compose "${compose_files[@]}" --project-name "$project_name" \
      --profile web-search logs --no-color >&2 || true
  fi
  docker compose "${compose_files[@]}" --project-name "$project_name" \
    --profile web-search down --volumes --remove-orphans >/dev/null 2>&1 || true
  if [[ "$temporary_directory" == /tmp/mongars-ci-smoke.* ]]; then
    rm -rf -- "$temporary_directory"
  fi
  exit "$exit_status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd -- "$repository_root"
docker info >/dev/null

install -m 0700 -d "$temporary_directory/secrets"
openssl rand -hex 32 > "$temporary_directory/secrets/postgres_password.txt"
openssl rand -hex 32 > "$temporary_directory/secrets/api_token.txt"
openssl rand -hex 32 > "$temporary_directory/secrets/approval_hmac_key.txt"
openssl rand -hex 32 > "$temporary_directory/secrets/searxng_secret.txt"
chmod 0640 "$temporary_directory"/secrets/*.txt

export MONGARS_ENVIRONMENT=production
export MONGARS_IMAGE="mongars:ci-smoke-$$"
export MONGARS_CADDY_IMAGE="mongars-caddy:ci-smoke-$$"
MONGARS_SECRET_GID="$(id -g)"
export MONGARS_SECRET_GID
export MONGARS_POSTGRES_PASSWORD_FILE="$temporary_directory/secrets/postgres_password.txt"
export MONGARS_API_TOKEN_FILE="$temporary_directory/secrets/api_token.txt"
export MONGARS_APPROVAL_HMAC_KEY_FILE="$temporary_directory/secrets/approval_hmac_key.txt"
export MONGARS_SEARXNG_SECRET_FILE="$temporary_directory/secrets/searxng_secret.txt"
export MONGARS_HTTPS_BIND_ADDRESS=127.0.0.1
export MONGARS_HTTPS_HOST=localhost
export MONGARS_HTTP_PORT=0
export MONGARS_HTTPS_PORT=0
export MONGARS_BIND_ADDRESS=127.0.0.1
export MONGARS_API_PORT=0
export MONGARS_WEB_SEARCH_ENABLED=true

docker compose "${compose_files[@]}" --project-name "$project_name" \
  --profile web-search build api https
docker compose "${compose_files[@]}" --project-name "$project_name" \
  --profile web-search up --no-build --detach --wait \
  postgres migrate ollama-mock search-mock search-egress-proxy searxng api https

# Resolve every host-facing smoke port assigned by Docker. Keeping all three
# dynamic lets independent CI/local runs execute concurrently without collision.
http_port="$(published_loopback_port 8080)"
https_port="$(published_loopback_port 8443)"
api_port="$(published_loopback_port 8001)"
if [[ -z "$http_port" || -z "$https_port" || -z "$api_port" ]]; then
  echo "Docker did not publish every expected smoke port" >&2
  exit 1
fi

ca_certificate="$temporary_directory/mongars-local-ca.crt"
docker compose "${compose_files[@]}" --project-name "$project_name" \
  cp https:/data/caddy/pki/authorities/local/root.crt "$ca_certificate" >/dev/null

# Plaintext HTTP serves only the local CA bootstrap artifact. It must neither
# expose nor redirect the authenticated application API.
downloaded_ca_certificate="$temporary_directory/downloaded-local-ca.crt"
curl --fail --silent --show-error \
  "http://localhost:$http_port/mongars-local-ca.crt" > "$downloaded_ca_certificate"
if ! cmp --silent "$ca_certificate" "$downloaded_ca_certificate"; then
  echo "plaintext CA endpoint did not return the active Caddy root certificate" >&2
  exit 1
fi

plaintext_api_status="$(
  curl --silent --output /dev/null --write-out '%{http_code}' \
    "http://localhost:$http_port/v1/healthz"
)"
if [[ "$plaintext_api_status" != "404" ]]; then
  echo "plaintext public API boundary returned HTTP $plaintext_api_status, expected 404" >&2
  exit 1
fi

# The separately published workstation-only listener remains usable on
# loopback for local administration and diagnostics.
curl --fail --silent --show-error \
  "http://127.0.0.1:$api_port/v1/healthz" >/dev/null

curl --fail --silent --show-error \
  --cacert "$ca_certificate" \
  "https://localhost:$https_port/v1/healthz" >/dev/null

unauthorized_status="$(
  curl --silent --output /dev/null --write-out '%{http_code}' \
    --cacert "$ca_certificate" \
    --header 'Authorization: Bearer invalid' \
    "https://localhost:$https_port/v1/tasks"
)"
if [[ "$unauthorized_status" != "401" ]]; then
  echo "HTTPS authentication smoke returned HTTP $unauthorized_status, expected 401" >&2
  exit 1
fi

api_token="$(tr -d '\n' < "$temporary_directory/secrets/api_token.txt")"
chat_response="$temporary_directory/chat-response.json"
curl --fail --silent --show-error \
  --cacert "$ca_certificate" \
  --header "Authorization: Bearer $api_token" \
  --header 'Content-Type: application/json' \
  --data '{"message":"Search the web for the deterministic deployment result.","web_search":"required","require_local_only":true}' \
  "https://localhost:$https_port/v1/chat" > "$chat_response"

python3 - "$chat_response" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["status"] == "ok"
assert payload["web_search_status"] == "ok"
assert payload["answer"] == "The deterministic deployment smoke result is verified."
assert payload["sources"] == [
    {
        "title": "Deterministic deployment result",
        "url": "https://example.com/mongars-ci-result",
    }
]
PY

https_user="$(
  docker compose "${compose_files[@]}" --project-name "$project_name" \
    exec -T https id -u
)"
if [[ "$https_user" == "0" ]]; then
  echo "Caddy deployment smoke is running as root" >&2
  exit 1
fi

echo "HTTPS authenticated required-search deployment smoke passed"
