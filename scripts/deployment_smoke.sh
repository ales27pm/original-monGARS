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
  postgres migrate ollama-mock search-mock search-egress-proxy searxng api worker https

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

# Detailed dependency state is protected even though minimal process liveness is public.
unauthenticated_readiness_status="$(
  curl --silent --output /dev/null --write-out '%{http_code}' \
    --cacert "$ca_certificate" \
    "https://localhost:$https_port/v1/readyz"
)"
if [[ "$unauthenticated_readiness_status" != "401" ]]; then
  echo "unauthenticated readiness returned HTTP $unauthenticated_readiness_status, expected 401" >&2
  exit 1
fi
api_token="$(tr -d '\n' < "$temporary_directory/secrets/api_token.txt")"

# Readiness must include the worker-owned parser and exact embedding-space
# heartbeat; process liveness alone cannot prove the document pipeline works.
readiness_response="$temporary_directory/readiness-response.json"
readiness_status=""
for _readiness_attempt in {1..45}; do
  readiness_status="$(
    curl --silent --show-error --output "$readiness_response" --write-out '%{http_code}' \
      --cacert "$ca_certificate" \
      --header "Authorization: Bearer $api_token" \
      "https://localhost:$https_port/v1/readyz"
  )"
  if [[ "$readiness_status" == "200" ]]; then
    break
  fi
  sleep 1
done
if [[ "$readiness_status" != "200" ]]; then
  echo "durable runtime readiness returned HTTP $readiness_status, expected 200" >&2
  cat "$readiness_response" >&2
  exit 1
fi
python3 - "$readiness_response" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
dependencies = payload["dependencies"]
assert payload["status"] == "ready"
assert dependencies["database"]["healthy"] is True
assert dependencies["inference"]["healthy"] is True
assert dependencies["web_search"]["healthy"] is True
assert dependencies["worker"]["healthy"] is True
assert dependencies["worker"]["component_id"] == "worker:primary"
assert dependencies["parser"]["healthy"] is True
embedding = dependencies["embedding_space"]
assert embedding["healthy"] is True
assert embedding["model_alias"] == "nomic-embed-text"
assert embedding["model_digest"] == (
    "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"
)
assert embedding["dimension"] == 768
assert embedding["total_chunk_count"] == 0
assert embedding["compatible_chunk_count"] == 0
assert embedding["legacy_chunk_count"] == 0
assert embedding["reindex_required"] is False
PY

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

# Exercise Main, Neurons, RM, and Hippocampus through the deployed HTTPS
# boundary. The API stages immutable bytes and creates an approval-gated task;
# only the worker parses, embeds, and persists the document.
document_marker="mongars-document-smoke-$$"
document_file="$temporary_directory/smoke-document.txt"
printf 'Deterministic document ingestion marker: %s\n' "$document_marker" > "$document_file"
document_size="$(wc -c < "$document_file" | tr -d '[:space:]')"
document_upload_response="$temporary_directory/document-upload-response.json"
curl --fail --silent --show-error \
  --cacert "$ca_certificate" \
  --header "Authorization: Bearer $api_token" \
  --form "file=@$document_file;type=text/plain;filename=smoke-document.txt" \
  --form-string "declared_size=$document_size" \
  --form-string 'source_timestamp=2026-01-02T03:04:05+00:00' \
  --form-string 'title=Deployment smoke document' \
  --form-string 'sensitivity=private' \
  --form-string 'retention_class=ttl_30d' \
  "https://localhost:$https_port/v1/documents" > "$document_upload_response"

read -r document_task_id document_action_digest < <(
  python3 - "$document_upload_response" <<'PY'
import json
import sys
import uuid

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
uuid.UUID(payload["id"])
assert payload["kind"] == "document.ingest"
assert payload["status"] == "waiting_approval"
assert payload["risk_level"] == "local_mutation"
assert len(payload["action_digest"]) == 64
int(payload["action_digest"], 16)
print(payload["id"], payload["action_digest"])
PY
)

document_review_response="$temporary_directory/document-review-response.json"
curl --fail --silent --show-error \
  --cacert "$ca_certificate" \
  --header "Authorization: Bearer $api_token" \
  "https://localhost:$https_port/v1/tasks/$document_task_id" \
  > "$document_review_response"
python3 - "$document_review_response" "$document_action_digest" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["kind"] == "document.ingest"
assert payload["status"] == "waiting_approval"
assert payload["action_digest"] == sys.argv[2]
assert payload["payload_summary"]["byte_length"] > 0
PY

document_payload_response="$temporary_directory/document-payload-response.json"
curl --fail --silent --show-error \
  --cacert "$ca_certificate" \
  --header "Authorization: Bearer $api_token" \
  "https://localhost:$https_port/v1/tasks/$document_task_id/payload?page=0" \
  > "$document_payload_response"
python3 - "$document_payload_response" "$document_action_digest" "$document_size" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    page = json.load(handle)
assert page["action_digest"] == sys.argv[2]
assert page["page_count"] == 1
payload = json.loads(page["content"])
assert payload["original_filename"] == "smoke-document.txt"
assert payload["detected_mime_type"] == "text/plain"
assert payload["byte_size"] == int(sys.argv[3])
assert "content" not in payload
PY

curl --fail --silent --show-error \
  --cacert "$ca_certificate" \
  --header "Authorization: Bearer $api_token" \
  --header 'Content-Type: application/json' \
  --data "{\"action_digest\":\"$document_action_digest\"}" \
  "https://localhost:$https_port/v1/tasks/$document_task_id/approve" >/dev/null

document_task_response="$temporary_directory/document-task-response.json"
document_task_status=""
for _ in $(seq 1 120); do
  curl --fail --silent --show-error \
    --cacert "$ca_certificate" \
    --header "Authorization: Bearer $api_token" \
    "https://localhost:$https_port/v1/tasks/$document_task_id" \
    > "$document_task_response"
  document_task_status="$(
    python3 - "$document_task_response" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["status"])
PY
  )"
  if [[ "$document_task_status" == "done" ]]; then
    break
  fi
  if [[ "$document_task_status" == "failed" || "$document_task_status" == "cancelled" ]]; then
    echo "document ingestion task entered terminal state: $document_task_status" >&2
    cat "$document_task_response" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$document_task_status" != "done" ]]; then
  echo "document ingestion task did not complete before the smoke timeout" >&2
  exit 1
fi

document_id="$(
  python3 - "$document_task_response" <<'PY'
import json
import sys
import uuid

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
result = payload["result"]
assert payload["kind"] == "document.ingest"
assert payload["attempt_count"] >= 1
assert result["created"] is True
assert result["chunk_count"] >= 1
uuid.UUID(result["document_id"])
provenance = result["provenance"]
assert provenance["validated_mime_type"] == "text/plain"
assert provenance["original_filename"] == "smoke-document.txt"
assert provenance["parser_name"] == "utf8-text"
assert provenance["extracted_character_count"] > 0
assert provenance["source_timestamp"] == "2026-01-02T03:04:05+00:00"
assert provenance["source_time_basis"] == "user_supplied"
assert isinstance(provenance["received_at"], str) and provenance["received_at"].endswith(
    "+00:00"
)
print(result["document_id"])
PY
)"

document_response="$temporary_directory/document-response.json"
curl --fail --silent --show-error \
  --cacert "$ca_certificate" \
  --header "Authorization: Bearer $api_token" \
  "https://localhost:$https_port/v1/memory/documents/$document_id" \
  > "$document_response"
python3 - "$document_response" "$document_id" "$document_task_id" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["id"] == sys.argv[2]
assert payload["source_type"] == "document"
assert payload["mime_type"] == "text/plain"
assert payload["title"] == "Deployment smoke document"
assert payload["sensitivity"] == "private"
assert payload["retention_class"] == "ttl_30d"
assert payload["metadata"]["ingestion_task_id"] == sys.argv[3]
PY

document_search_response="$temporary_directory/document-search-response.json"
curl --fail --silent --show-error \
  --cacert "$ca_certificate" \
  --header "Authorization: Bearer $api_token" \
  --header 'Content-Type: application/json' \
  --data "{\"query\":\"$document_marker\",\"top_k\":10,\"mode\":\"hybrid\"}" \
  "https://localhost:$https_port/v1/memory/search" > "$document_search_response"
python3 - "$document_search_response" "$document_id" "$document_marker" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
matches = [
    hit
    for hit in payload["hits"]
    if hit["document_id"] == sys.argv[2] and sys.argv[3] in hit["text"]
]
assert matches
PY

https_user="$(
  docker compose "${compose_files[@]}" --project-name "$project_name" \
    exec -T https id -u
)"
if [[ "$https_user" == "0" ]]; then
  echo "Caddy deployment smoke is running as root" >&2
  exit 1
fi

echo "HTTPS auth, required search, and approved document-ingestion deployment smoke passed"

artifacts_directory="$repository_root/artifacts"
artifacts_evidence_path="$artifacts_directory/deployment-smoke-evidence.json"
mkdir -m 0700 -p "$artifacts_directory"
MONGARS_DEPLOYMENT_SMOKE_EVIDENCE_PATH="$artifacts_evidence_path" \
  http_port="$http_port" \
  https_port="$https_port" \
  api_port="$api_port" \
  project_name="$project_name" \
  repository_root="$repository_root" \
  python3 - <<'PY'
import json
import os
import platform
from datetime import datetime, timezone

evidence_path = os.environ["MONGARS_DEPLOYMENT_SMOKE_EVIDENCE_PATH"]
with open(evidence_path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "check": "deployment-smoke",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "runner": os.getenv("RUNNER_NAME", os.getenv("RUNNER_OS", "local")),
            "git_sha": os.getenv("GITHUB_SHA", "local"),
            "ports": {
                "http": int(os.environ["http_port"]),
                "https": int(os.environ["https_port"]),
                "api": int(os.environ["api_port"]),
            },
            "project_name": os.environ["project_name"],
            "project_root": os.environ["repository_root"],
            "checks": [
                "plaintext-ca",
                "plaintext-healthz-boundary",
                "api-workstation-liveness",
                "https-healthz",
                "https-readyz-auth-boundary",
                "https-readyz-authenticated",
                "readyz-embedding-metadata",
                "required-chat-web-search",
                "document-ingest-approval-path",
                "document-approval-and-reindex-readyz",
            ],
            "ollama_context_length": int(os.getenv("MONGARS_OLLAMA_CONTEXT_LENGTH", "4096")),
            "chat_model": os.getenv("MONGARS_OLLAMA_CHAT_MODEL", "qwen3:4b-instruct"),
            "embedding_model": os.getenv("MONGARS_OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
            "parser_max_concurrency": 2,
            "api_approval_limits": {
                "global_upload_concurrency": int(
                    os.getenv("MONGARS_MAX_CONCURRENT_DOCUMENT_UPLOADS", "2")
                ),
                "per_owner_upload_concurrency": int(
                    os.getenv("MONGARS_MAX_CONCURRENT_DOCUMENT_UPLOADS_PER_OWNER", "1")
                ),
            },
            "model": "qwen3:4b-instruct (chat) + nomic-embed-text (embedding)",
        },
        handle,
        indent=2,
        sort_keys=True,
    )
PY
