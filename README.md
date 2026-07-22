# monGARS

monGARS is a local-first personal-agent control plane for a single-owner Ubuntu workstation.
This repository contains the production kernel: a typed FastAPI API, PostgreSQL/pgvector
memory, a durable worker queue, explicit approval gates, and a backend-neutral inference
boundary with an Ollama implementation.

The first release intentionally supports only two bounded actions:

- `memory.search` is read-only and can be queued without approval.
- `memory.note.create` writes to PostgreSQL and requires an expiring, single-use approval.

The model cannot execute tools, choose a backend URL, supply an owner identity, or grant its
own approval. Shell execution, arbitrary HTTP tools, remote inference, OCR, and autonomous
self-modification are not part of this release.

## Architecture

```text
client -> FastAPI -> Cortex -> Ollama
                    |   |
                    |   +-> PostgreSQL + pgvector (Hippocampus)
                    +-----> task_queue -> worker (RM)
                                      -> deterministic policy + approval digest
```

All user-owned reads are scoped with the server-derived principal. Retrieved text is passed
to the model as explicitly untrusted JSON data. Privileged task approval binds the owner,
validated payload, action kind, policy version, and expiry with HMAC; the worker verifies the
digest again immediately before the database mutation.

## Prerequisites

- Python 3.12, `uv` 0.11.30, and ShellCheck for local development
- Docker Engine with the Compose plugin
- NVIDIA driver and NVIDIA Container Toolkit for the optional Ollama GPU profile
- An RTX 2070 or similar CUDA GPU is sufficient for a quantized 3B–8B model at a conservative
  context size; the supplied Ollama profile defaults to 4096 tokens

Verify the host before enabling the GPU profile:

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
```

## Production-style Compose startup

Create local configuration and secret files. The `secrets/` directory is ignored by Git.

```bash
cp .env.example .env
install -m 0700 -d secrets
openssl rand -hex 32 > secrets/postgres_password.txt
openssl rand -hex 32 > secrets/api_token.txt
openssl rand -hex 32 > secrets/approval_hmac_key.txt
openssl rand -hex 32 > secrets/searxng_secret.txt
chgrp "$(id -g)" secrets/*.txt
chmod 0640 secrets/*.txt
```

Set `MONGARS_SECRET_GID` in `.env` to the output of `id -g` if it is not `1000`. The
application user is added only to that supplementary group so file-backed Compose secrets are
readable without making them world-readable.

Build the immutable application image, start PostgreSQL and Ollama, then pull the configured
models:

```bash
docker compose --profile gpu build
docker compose --profile gpu up -d postgres ollama
docker compose exec ollama ollama pull qwen3:4b-instruct
docker compose exec ollama ollama pull nomic-embed-text
docker compose --profile gpu --profile web-search up -d --wait
```

The `web-search` profile and `MONGARS_WEB_SEARCH_ENABLED=true` opt in to public-web egress; omit the
profile and set the variable to `false` to disable it completely. SearXNG has no host port or direct
external interface and is reachable only on dedicated internal networks. Its engine traffic uses a
fixed Squid proxy that resolves and rejects private, loopback, link-local, Docker-host, and cloud
metadata destinations before allowing ports 80 or 443. Cortex performs a
network search only when the chat text explicitly asks to search or browse the public web, or when
an API caller sends `"web_search":"required"`. Search queries and the configured engines'
requests leave the workstation; inference, memory, and orchestration remain local. Send
`"web_search":"off"` to prohibit search for a turn. Results are bounded, treated as untrusted
data, and returned separately in the response's `sources` array.
SearXNG container logging is disabled because upstream engine errors can include the complete
private query URL; proxy logging is disabled for the same reason. Use the API readiness status,
`deploy/egress-proxy/check.sh`, and `deploy/searxng/check.sh` for diagnostics.

Caddy publishes the control plane on `127.0.0.1:8000` by default while the application container,
PostgreSQL, and Ollama stay on internal Compose networks. The migration one-shot service must
complete successfully before the API or worker starts.

```bash
curl -fsS http://127.0.0.1:8000/v1/healthz
curl -fsS http://127.0.0.1:8000/v1/readyz
```

### Bundled web interface

The API image includes a responsive, dependency-free control surface at `/`. It provides chat,
memory search, protected note creation, exact task-payload review, approvals, cancellation, and
live readiness status:

```text
http://127.0.0.1:8000/
```

The loopback URL is workstation-only. Use the HTTPS setup below before opening the interface from
an iPhone; the API's plaintext port should not be published to the LAN.

### Secure iPhone and LAN access

Keep `MONGARS_BIND_ADDRESS=127.0.0.1` and put an HTTPS reverse proxy in front of the API.
The included Caddy service can terminate HTTPS directly on a reserved LAN address. Configure the
address as both its bind address and certificate subject, and allow that host through FastAPI:

```dotenv
MONGARS_BIND_ADDRESS=127.0.0.1
MONGARS_HTTPS_BIND_ADDRESS=10.0.0.154
MONGARS_HTTPS_HOST=10.0.0.154
MONGARS_TRUSTED_HOSTS=["10.0.0.154","localhost","127.0.0.1"]
```

Start the stack and verify the proxy from the workstation:

```bash
docker compose --profile gpu --profile web-search up -d --wait
curl -fsS http://10.0.0.154/mongars-local-ca.crt \
  --output /tmp/mongars-local-ca.crt
openssl x509 -in /tmp/mongars-local-ca.crt \
  -noout -subject -fingerprint -sha256
curl --fail --silent --show-error \
  --cacert /tmp/mongars-local-ca.crt \
  https://10.0.0.154/v1/readyz
```

On the iPhone, open `http://10.0.0.154/mongars-local-ca.crt` in Safari and install the downloaded
profile under **Settings > General > VPN & Device Management**. Then explicitly enable the root
under **Settings > General > About > Certificate Trust Settings**. Only the public root certificate
is downloadable; Caddy's CA private key remains inside its Docker volume. Compare the certificate's
SHA-256 fingerprint with the value printed directly on the workstation before enabling trust.
Reserve the workstation's LAN address so the certificate subject does not change.

Open `https://10.0.0.154/v1/healthz` in Safari to confirm trust. The web interface and native app can
then use `https://10.0.0.154` without weakening bearer-token transport. Plain HTTP serves only the
public CA download and never proxies API requests.

The local CA persists in the `caddy_data` volume. Do not run `docker compose down -v` unless every
device can be re-enrolled with a newly generated root certificate.
The supplied Caddy image removes the upstream binary's unnecessary low-port file capability and
runs as UID/GID `65534` with an empty capability set. A networkless one-shot service changes
ownership of an existing root-owned Caddy volume before startup, preserving previously enrolled
local CAs during the non-root migration.

Alternatively, Tailscale Serve can terminate HTTPS for a tailnet hostname and proxy to the
loopback-only service:

```bash
sudo tailscale serve --bg http://127.0.0.1:8000
```

Add the exact HTTPS hostname to `MONGARS_TRUSTED_HOSTS` in `.env`, alongside the loopback
defaults. For example:

```dotenv
MONGARS_TRUSTED_HOSTS=["workstation.example-tailnet.ts.net","localhost","127.0.0.1"]
```

Caddy, Nginx, or Traefik is also appropriate when it is configured with a certificate trusted
by the iPhone and proxies only to `127.0.0.1:8000`. Set the proxy request-body limit to the
same value as (or lower than) `MONGARS_MAX_REQUEST_BYTES`, so oversized uploads are rejected
before they reach the application. For example, an Nginx TLS `server` block should include:

```nginx
client_max_body_size 2100000;

location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
}
```

Set `MONGARS_CORS_ORIGINS` to the exact HTTPS web origin if a separate browser frontend calls
the API. Never send the bearer token over plaintext LAN HTTP, publish Ollama, or expose
PostgreSQL.

### Expo Go iOS client

The native Expo SDK 54 client lives in `apps/mobile`. Configure its public API origin, install the
locked npm dependencies, and start Metro on the LAN:

```bash
cd apps/mobile
cp .env.example .env.local
npm ci
npm run start:lan
```

Scan Metro's QR code with Expo Go. The client stores the bearer token in the iOS Keychain through
`expo-secure-store` and refuses to save or transmit it to non-loopback plaintext HTTP. Set
`EXPO_PUBLIC_MONGARS_API_URL` to the trusted HTTPS hostname described above for authenticated chat,
memory, and task operations.

Read the bearer token without printing it into shell history:

```bash
read -r MONGARS_TOKEN < secrets/api_token.txt
curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"message":"What can you help me with?"}' \
  http://127.0.0.1:8000/v1/chat
```

### Approved memory write

Creating a note returns a task in `waiting_approval`, not an immediate write:

```bash
TASK_ID="$(curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"text":"The workstation is in Toronto.","title":"Location"}' \
  http://127.0.0.1:8000/v1/memory/documents \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

# Review the bounded summary and HMAC-bound action digest. Fetch exact JSON one
# bounded page at a time; page is zero-based.
REVIEW="$(curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "http://127.0.0.1:8000/v1/tasks/${TASK_ID}")"
ACTION_DIGEST="$(printf '%s' "${REVIEW}" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["action_digest"])')"
curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "http://127.0.0.1:8000/v1/tasks/${TASK_ID}/payload?page=0"

curl -fsS -X POST -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"action_digest\":\"${ACTION_DIGEST}\"}" \
  "http://127.0.0.1:8000/v1/tasks/${TASK_ID}/approve"

curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "http://127.0.0.1:8000/v1/tasks/${TASK_ID}"
```

The worker embeds and persists the note after approval. Approval expires after 15 minutes by
default and cannot be replayed.

## Local development

Install the exact locked environment with `uv`:

```bash
uv python install 3.12
uv sync --frozen --extra dev --extra documents
```

Use the development override to publish PostgreSQL only on loopback:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up -d postgres
export MONGARS_DATABASE_URL='postgresql+psycopg://mongars:YOUR_PASSWORD@127.0.0.1:5432/mongars'
export MONGARS_API_TOKEN='development-token'
export MONGARS_APPROVAL_HMAC_KEY='development-approval-key'
uv run alembic upgrade head
uv run uvicorn mongars.main:app --reload
uv run mongars-worker
```

Run the full validation gate with one command:

```bash
make ci-local
```

`ci-local` ignores caller-provided database URLs, provisions a pinned pgvector container on a
Docker-assigned loopback port, runs migrations and every Python/mobile/deployment gate, builds the
production image, verifies non-root runtimes and bounded search egress, and removes disposable
resources on exit. The suite enforces 80% branch coverage; the current baseline is above that
threshold. Direct
integration-test invocations still require `MONGARS_TEST_DATABASE_URL` and skip when it is absent.

On a representative retained corpus, capture both the planner's natural choice and an
index-forced comparison with `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`:

```bash
uv run python scripts/benchmark_memory_search.py \
  --owner-id local-owner \
  --candidate-count 64 > memory-search-plan.json
```

The normal plan is the production signal; the forced plan proves HNSW eligibility when a small
development corpus would otherwise make a sequential scan cheaper. Re-run this benchmark after
large ingest batches, PostgreSQL upgrades, or changes to embedding dimensions and ANN settings.

### Runtime and inference smoke tests

After starting the production-like Compose stack, exercise the actual API, approval, worker,
memory, authentication, and readiness paths. The cleanup option deletes only artifacts bearing
the unique IDs created by that smoke run:

```bash
uv run python scripts/runtime_smoke.py --cleanup-with-compose
```

Real model execution remains opt-in and is not part of standard CI. Expose Ollama on loopback
only for the duration of the test, then restore the production topology:

```bash
docker compose -f compose.yaml -f compose.inference-test.yaml --profile gpu up -d --wait ollama
MONGARS_RUN_INFERENCE_TESTS=1 \
MONGARS_OLLAMA_BASE_URL=http://127.0.0.1:11434 \
  uv run pytest -q tests/inference
docker compose --profile gpu --profile web-search up -d --wait
```

The main CI workflow pins actions by commit SHA and runs Bandit, pip-audit, split unit/database
tests, coverage, migration, Compose, Caddy/SearXNG/egress checks, mobile npm lint/type/test/audit,
and a disposable HTTPS + authentication + required-search deployment smoke. The separate
supply-chain workflow adds Gitleaks plus Trivy HIGH/CRITICAL scanning and an SPDX SBOM for every
deployed image: monGARS, Caddy, PostgreSQL/pgvector, Ollama, SearXNG, and the egress proxy.

## HTTP API

| Endpoint | Purpose | Authentication |
|---|---|---|
| `GET /v1/healthz` | Process liveness | No |
| `GET /v1/readyz` | PostgreSQL, inference-model, and configured web-search readiness | No |
| `POST /v1/chat` | Local chat with owner-scoped retrieval and opt-in web search | Bearer |
| `POST /v1/memory/search` | Semantic or hybrid memory search | Bearer |
| `POST /v1/memory/documents` | Propose an approved text-note write | Bearer |
| `GET /v1/memory/documents/{id}` | Read owner-scoped document metadata | Bearer |
| `POST /v1/tasks` | Create a registered, schema-validated task | Bearer |
| `GET /v1/tasks` | List owner-scoped task summaries | Bearer |
| `GET /v1/tasks/{id}` | Read a bounded payload summary and action digest | Bearer |
| `GET /v1/tasks/{id}/payload?page={index}` | Read one bounded exact payload page | Bearer |
| `POST /v1/tasks/{id}/approve` | Approve with the reviewed `action_digest` (never the payload) | Bearer |
| `POST /v1/tasks/{id}/cancel` | Cancel a queued task | Bearer |

OpenAPI documentation is available at `/docs` outside production mode.

## Configuration and security

Configuration uses the `MONGARS_` prefix and is validated at process startup. Production mode
rejects development secret sentinels. Remote inference is disabled by default; non-loopback
backends require both an explicit opt-in and TLS. Do not publish Ollama directly.

Important production controls in the supplied Compose stack include non-root app and Caddy users,
read-only root filesystems, dropped Linux capabilities, `no-new-privileges`, bounded tmpfs,
loopback-only API publication, internal-only database/inference networks, and file-mounted
secrets. Caddy is the only host-facing container; the API reaches SearXNG over a separate internal
network, SearXNG reaches only the internal proxy network, and only the ACL proxy joins the ordinary
search-egress bridge. The supplied base, PostgreSQL, Ollama, SearXNG, Squid, and Caddy images are
pinned by digest; update those digests deliberately as part of a reviewed release.

Full-disk encryption and encrypted backups remain host responsibilities. Embeddings and
plaintext memory chunks are sensitive data. TTL deletion removes the live document and its
cascaded chunks; backup retention must be configured to match the same privacy policy.

## Migrations, backup, and rollback

Never run destructive schema changes as an implicit application startup action. The Compose
`migrate` service runs the checked-in additive migration before deployment.

```bash
docker compose run --rm migrate
docker compose exec postgres pg_dump -U mongars -Fc mongars > mongars.dump
```

Release images should be tagged immutably. Roll back the API and worker image tag separately
from schema rollback. The initial migration downgrade drops application tables and is only
appropriate for a disposable database.

## Current boundary and next slices

This release is a production-capable control-plane foundation, not the entire long-term platform.
The next bounded slices are native PDF/DOCX ingestion with parser isolation, an OpenTelemetry
Collector/Prometheus profile, and additional registered tools only after their sandbox and approval
contracts have dedicated adversarial tests.
