# monGARS

monGARS is a local-first personal-agent control plane for a single-owner Ubuntu workstation.
This repository contains the production kernel: a typed FastAPI API, PostgreSQL/pgvector
memory, a durable worker queue, explicit approval gates, a dedicated Neurons semantic-processing
boundary, and a secure Main document-ingestion pipeline.

The current release intentionally supports only these bounded actions:

- `memory.search` is read-only and can be queued without approval.
- `memory.note.create` writes to PostgreSQL and requires an expiring, single-use approval.
- `memory.reindex` adds active-space vectors to an owner-scoped corpus and requires an expiring,
  single-use approval.
- `document.ingest` stages a validated TXT, Markdown, HTML, PDF, or DOCX upload and requires
  approval before a worker parses, embeds, or persists its text.

The model cannot execute tools, choose a backend URL, supply an owner identity, or grant its
own approval. Shell execution, arbitrary HTTP tools, remote inference, OCR, and autonomous
self-modification are not part of this release.

## Architecture

```text
client -> FastAPI -> Cortex -------------------------> Ollama chat
          |             |
          |             +----------------------------> Hippocampus
          |                                            PostgreSQL + pgvector
          |
          +-> bounded document staging -> task_queue -> worker (RM)
                                              |
                                              +-> isolated parser (Main)
                                              +-> EmbeddingService (Neurons)
                                                        |
                                                        +-> fixed Ollama embedding model
```

All user-owned reads are scoped with the server-derived principal. Retrieved text is passed
to the model as explicitly untrusted JSON data. Privileged task approval binds the owner,
validated payload, action kind, policy version, and expiry with HMAC; the worker verifies the
digest again immediately before the database mutation.

Neurons is database-independent: it validates and bounds text batches, pins the provider and
model identity, verifies vector count and dimension, rejects non-finite values, and emits only
non-content execution metrics. Main validates the upload envelope in the API, stages immutable
bytes in PostgreSQL, and performs format-specific extraction in a disposable worker child process.
Parsing and embedding both occur outside open database transactions.

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

For ARM64/Jetson targets, keep the GPU path disabled and use the ARM/Jetson profile overrides instead:

```bash
docker compose -f compose.yaml -f compose.arm64.yaml --profile arm64 build
docker compose -f compose.yaml -f compose.arm64.yaml --profile arm64 up -d postgres ollama

# Jetson example (set MONGARS_OLLAMA_IMAGE_JETSON for your device image first)
docker compose -f compose.yaml -f compose.jetson.yaml --profile jetson build
docker compose -f compose.yaml -f compose.jetson.yaml --profile jetson up -d postgres ollama
```

### Hardware profiles and support envelope

The supplied compose stack defines three explicit deployment profiles:

- `gpu`: CUDA-enabled desktop/server builds using NVIDIA runtime devices.
- `arm64`: CPU-only ARM64 builds that override only the Ollama image.
- `jetson`: Jetson-class ARM64 builds with explicit Jetson image selection.

The defaults in this branch are intentionally conservative and shared across CPU-only profiles:

| Profile | Host memory / CPU target | GPU requirement | Ollama context | Default models | Concurrency ceilings |
|---|---:|---|---|---|---|
| `gpu` | API/worker 1GiB each, parser 768MiB; compose defaults | one GPU via `deploy.resources.reservations.devices` | `MONGARS_OLLAMA_CONTEXT_LENGTH=4096` | `qwen3:4b-instruct`, `nomic-embed-text` | API: 1 process (`uvicorn --workers 1`); parser: `--limit-concurrency 2`; parser memory/worker are single-process |
| `arm64` | Same as base compose defaults; no explicit GPU reservations | none by default | `MONGARS_OLLAMA_CONTEXT_LENGTH=4096` | `qwen3:4b-instruct`, `nomic-embed-text` | Same service ceilings as `gpu`, with `MONGARS_OLLAMA_IMAGE_ARM64` controlling image |
| `jetson` | Same as base compose defaults with runtime-specific image override | NVIDIA Jetson runtime assumptions | `MONGARS_OLLAMA_CONTEXT_LENGTH=4096` | `qwen3:4b-instruct`, `nomic-embed-text` | Same service ceilings as `gpu`, with `MONGARS_OLLAMA_IMAGE_JETSON` controlling image |

For each support claim, keep reproducible artifacts in CI:

- `artifacts/arm64-profile-validation.json` (ARM64 and Jetson compose validation + image build + unit tests)
- `artifacts/deployment-smoke-evidence.json` (HTTPS auth, required search, and approved ingestion smoke trace)

Use these files when updating deployment profile support statements for a new hardware class.

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
read -r MONGARS_TOKEN < secrets/api_token.txt
curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  http://127.0.0.1:8000/v1/readyz
```

The minimal liveness endpoint is public. Detailed readiness is bearer-protected because it reports
worker identity, deployed versions, model identity, and owner-corpus state.

### Bundled web interface

The API image includes a responsive, dependency-free control surface at `/`. It provides chat,
memory search, protected note creation, approved document uploads, exact task-payload review,
approvals, cancellation, and live readiness status:

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
read -r MONGARS_TOKEN < secrets/api_token.txt
curl --fail --silent --show-error \
  --cacert /tmp/mongars-local-ca.crt \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
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
same value as (or lower than) `MONGARS_MAX_REQUEST_BYTES` for ordinary routes and
`MONGARS_MAX_DOCUMENT_REQUEST_BYTES` for `POST /v1/documents`, so oversized bodies are rejected
before they reach the application. The included Caddyfile applies those limits separately. For
example, an Nginx TLS `server` block can use:

```nginx
client_max_body_size 2100000;

location = /v1/documents {
    client_max_body_size 10500000;
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
}

location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
}
```

Set `MONGARS_CORS_ORIGINS` to the exact HTTPS web origin if a separate browser frontend calls
the API. Never send the bearer token over plaintext LAN HTTP, publish Ollama, or expose
PostgreSQL.

### Expo iOS development client

The native Expo SDK 54 client lives in `apps/mobile` and requires the repository's pinned
development build. **Expo Go is unsupported** and cannot load this runtime. Install the locked npm
dependencies, authenticate EAS CLI, build the internal-distribution client, install that client on
the registered iPhone, and only then start Metro on the LAN:

```bash
cd apps/mobile
cp .env.example .env.local
npm ci
npx --yes eas-cli@21.0.3 login
npm run build:development:ios
npm run start:dev-client:lan
```

Open the installed **monGARS** development client—not Expo Go—and scan Metro's QR code. The
`development` EAS profile is an SDK 54 development client on the pinned `sdk-54` builder image;
`npm run start:lan` is retained only as an alias for the dev-client LAN command. The client stores
the bearer token and normalized server origin together in the iOS Keychain through
`expo-secure-store`, and refuses to save or transmit credentials to non-loopback plaintext HTTP.
Set `EXPO_PUBLIC_MONGARS_API_URL` only to the trusted HTTPS hostname described above, or select that
origin in Settings after installation. Never put the bearer token in an `EXPO_PUBLIC_` variable.

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

### Neurons and approved document ingestion

Neurons is implemented by `mongars.embeddings`. `EmbeddingService` is the only semantic-vector
boundary used by memory ingestion and retrieval. The initial provider is the fixed
`nomic-embed-text` Ollama model with the 768-dimensional vector shape required by the current
pgvector schema. Document chunks are prepared with the exact `search_document: ` prefix and search
queries with the exact `search_query: ` prefix; each prefix ends in one ASCII space. Truncation is
disabled, so an over-limit input fails instead of silently producing a vector for incomplete text.

The mutable Ollama alias is resolved through `/api/tags` to the reviewed artifact digest
`0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`. Neurons pins that digest
for the process lifetime and derives a SHA-256 `EmbeddingSpace.space_id` from the provider, alias,
artifact digest, dimension, normalization policy, all four purpose instructions, truncation
policy, maximum prepared-input bytes, and profile version. The shipped space uses provider
`ollama`, normalization policy `none`, an 8,192-byte prepared-input ceiling, truncation `false`,
and profile `nomic-v1.5-rag-v1`. Its immutable identifier is persisted as
`embedding_space_id`; vectors from another artifact or preparation profile are never mixed into
active retrieval. `MONGARS_OLLAMA_EMBEDDING_MODEL_DIGEST` can pin a reviewed replacement digest,
but changing it intentionally creates a new space and requires the approved shadow-reindex
workflow below.

The service never accesses PostgreSQL, never silently changes providers or models, bounds and
splits batches, validates every returned vector, rejects NaN and infinity, and does not put source
text into metrics or logs. A duplicate whose existing chunks lack a complete vector set in the
active embedding space fails with an explicit reindex-required conflict; it is never reported as
an idempotent success while remaining invisible to active-space retrieval.

Main is implemented by `mongars.ingestion`; it does not use a `mongars.main` package because
`mongars/main.py` is the FastAPI entry point. The supported, non-OCR extraction paths are:

| Upload | Declared MIME type | Parser and enforced boundary | Structured locator |
|---|---|---|---|
| `.txt` | `text/plain` | Strict UTF-8 decoding and normalized plain text | Zero-based `block_index`; one-based `line_start` and `line_end` |
| `.md`, `.markdown` | `text/markdown` | Strict UTF-8 and plain-text Markdown normalization; no rendering or code execution | `block_index`, one-based source-line range, and current `heading_path` |
| `.html`, `.htm` | `text/html` | Beautiful Soup text extraction; active/inline-styled content is rejected, hidden/non-content nodes are removed, and URLs are never fetched | `block_index` and `heading_path`; table cells also carry zero-based `table_index` and an A1-style `cell_reference` |
| `.pdf` | `application/pdf` | `pypdf` born-digital text extraction; encrypted PDFs and image-only/OCR documents are unsupported | One-based `page_number` plus a zero-based block index within that page |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Bounded ZIP inspection plus `python-docx`; external relationships, traversal, symlinks, encryption, corruption, and suspicious compression are rejected | Document-order `block_index` and `heading_path`; table cells also carry `table_index` and `cell_reference` |

Every parser result uses the same exact locator object keys: `media_type`, `page_number`,
`heading_path`, `block_index`, `line_start`, `line_end`, `table_index`, and `cell_reference`.
Coordinates that do not apply to a format are `null` (or `[]` for `heading_path`). Chunking never
crosses a parser segment, narrows source-line ranges when it splits TXT or Markdown, and persists
the locator with every memory chunk. `POST /v1/memory/search` returns it as each hit's `locator`, so
the client can cite a page, source-line range, heading, or exact table cell without parsing prose.

The filename extension, declared MIME type, and detected byte signature must agree. A successful
upload does not parse or persist document text. It creates an expiring PostgreSQL staging object
and a `document.ingest` task in `waiting_approval`. The task's exact reviewed payload contains the
SHA-256 digest, normalized filename, detected MIME type, byte size, user-supplied source timestamp,
trusted receipt timestamp, source-time basis, sensitivity, retention class, title, and staging
identifier—not the uploaded bytes. Approval binds that payload to its HMAC action digest. The
worker then re-hashes and revalidates the staged object before doing any work:

```text
multipart upload -> envelope validation -> bounded staging + waiting_approval task
    -> exact metadata review -> approval -> worker ownership check
    -> secretless parser sidecar -> disposable parser process -> bounded normalized text
    -> Neurons embedding -> Hippocampus document/chunks/provenance
    -> document_ingested + task_completed events -> staging deletion
```

`source_timestamp` is required multipart metadata supplied by the client, must include a timezone,
and is normalized to UTC. It describes the source file but is not trusted as server time.
`received_at` is captured by the API immediately after bearer authentication and upload-capacity
admission, before multipart parsing; the client cannot submit or replace it. This release therefore
sets `source_time_basis` to the exact value `user_supplied`, persists both timestamps, binds both to
the approved task, and uses trusted `received_at` for staging expiry. The worker rejects any staged
record whose timestamps or basis no longer match that approved payload.

Use the loopback endpoint locally, or replace `MONGARS_ORIGIN` with the trusted HTTPS origin for
another device. The example deliberately reads the bearer token from its secret file instead of
putting it in shell history:

```bash
read -r MONGARS_TOKEN < secrets/api_token.txt
MONGARS_ORIGIN=http://127.0.0.1:8000
DOCUMENT_PATH=./example.txt
DOCUMENT_MIME=text/plain
DOCUMENT_SIZE="$(wc -c < "${DOCUMENT_PATH}" | tr -d '[:space:]')"
SOURCE_TIMESTAMP="$(date -u --reference="${DOCUMENT_PATH}" '+%Y-%m-%dT%H:%M:%SZ')"

UPLOAD_RESPONSE="$(curl -fsS \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -F "file=@${DOCUMENT_PATH};type=${DOCUMENT_MIME}" \
  -F "declared_size=${DOCUMENT_SIZE}" \
  -F "source_timestamp=${SOURCE_TIMESTAMP}" \
  -F 'title=Example document' \
  -F 'sensitivity=private' \
  -F 'retention_class=keep' \
  "${MONGARS_ORIGIN}/v1/documents")"

TASK_ID="$(printf '%s' "${UPLOAD_RESPONSE}" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
ACTION_DIGEST="$(printf '%s' "${UPLOAD_RESPONSE}" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["action_digest"])')"

# Review the bounded summary, then every zero-based exact metadata page before approval.
curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/tasks/${TASK_ID}"
curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/tasks/${TASK_ID}/payload?page=0"

curl -fsS -X POST \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"action_digest\":\"${ACTION_DIGEST}\"}" \
  "${MONGARS_ORIGIN}/v1/tasks/${TASK_ID}/approve"

# Poll until status is done or failed, then use result.document_id with the
# owner-scoped GET /v1/memory/documents/{id} endpoint.
curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/tasks/${TASK_ID}"
```

Choose the declared MIME type from the table; do not use `application/octet-stream`. The upload
response is `202 Accepted` and always requires a separate approval. Cancelling the task deletes its
staging object. Expired approval/staging and deterministic document rejections are recorded and
cleaned up; retryable parser-infrastructure or embedding failures retain staging only while the
durable task remains eligible for retry.

The API authenticates and takes a process-local upload permit before FastAPI reads any multipart
bytes. By default it admits at most two concurrent uploads in one API process and one for a given
owner. Excess requests fail immediately with `429`, `Retry-After: 1`, and `document upload
concurrency limit reached`; cancellation and all response paths release the permit in `finally`.
PostgreSQL separately serializes per-owner staging quota accounting, so concurrent requests cannot
race the durable object/byte ceilings. When horizontally scaling the API, apply an aggregate limit
at the HTTPS proxy as well because these early body-admission counters are intentionally
process-local.

#### Approved shadow reindex

`POST /v1/memory/reindex` proposes a `memory.reindex` local mutation; it never starts embedding
until the exact task payload has been reviewed and approved. Omit `document_id` to cover every
chunk owned by the authenticated principal, or provide one owner-scoped document UUID. The
reviewed `batch_size` defaults to 32 and is constrained to 1–128:

```bash
REINDEX_RESPONSE="$(curl -fsS -X POST \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"batch_size":32}' \
  "${MONGARS_ORIGIN}/v1/memory/reindex")"
REINDEX_TASK_ID="$(printf '%s' "${REINDEX_RESPONSE}" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"

REINDEX_REVIEW="$(curl -fsS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/tasks/${REINDEX_TASK_ID}")"
REINDEX_ACTION_DIGEST="$(printf '%s' "${REINDEX_REVIEW}" \
  | python -c 'import json,sys; print(json.load(sys.stdin)["action_digest"])')"
curl -fsS -X POST \
  -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d "{\"action_digest\":\"${REINDEX_ACTION_DIGEST}\"}" \
  "${MONGARS_ORIGIN}/v1/tasks/${REINDEX_TASK_ID}/approve"
```

The worker selects only chunks that lack a vector in the active `embedding_space_id`, commits that
bounded selection, embeds their text with purpose `search_document` outside a database
transaction, and inserts active-space rows in a new short transaction. Existing vector rows are
not removed: this is a retry-safe shadow write keyed by `(chunk_id, embedding_space_id)`. The final
task result reports `embedding_space_id`, `model_alias`, `model_digest`,
`reindexed_chunk_count`, `compatible_chunk_count`, `legacy_chunk_count`, and `reindex_required`;
the `memory_reindexed` autobiographical event records the bounded outcome.

#### Durable runtime and corpus readiness

The bearer-protected `GET /v1/readyz` route is ready only when PostgreSQL, inference, configured web
search, and the durable worker-owned document path agree. The worker publishes its version, Git SHA,
parser health/version, and exact embedding-space identity to PostgreSQL every
`MONGARS_WORKER_RUNTIME_HEARTBEAT_SECONDS`. The API marks it stale after
`MONGARS_WORKER_RUNTIME_STALE_SECONDS`, independently resolves its own active embedding space, and
compares the two identities. This prevents an API-only health check from hiding a stopped worker,
offline parser, or worker/API model mismatch.

The `dependencies.embedding_space` object is also the owner-scoped corpus gate. Its
`total_chunk_count` counts every chunk for `MONGARS_OWNER_ID`, `compatible_chunk_count` counts
chunks with a vector in the active space, and `legacy_chunk_count` is their difference.
`reindex_required` is true whenever that legacy count is nonzero. In that case the dependency has
status `reindex_required`, error code `embedding_reindex_required`, and readiness returns HTTP 503
until the approved shadow reindex completes. Inspect the full stable shape with:

```bash
curl -sS -H "Authorization: Bearer ${MONGARS_TOKEN}" \
  "${MONGARS_ORIGIN}/v1/readyz" | python -m json.tool
```

The relevant dependency keys are `worker` (`status`, `component_id`, `instance_id`, `version`,
`git_sha`, `last_seen_at`, and `age_seconds`), `parser` (`healthy`, `version`, `error_code`), and
`embedding_space` (`status`, `space_id`, `model_alias`, `model_digest`, `dimension`,
`worker_space_id`, all three corpus counts, `reindex_required`, and `error_code`). A completely
empty owner corpus is compatible and does not require reindexing.

For day-to-day operations, use the status helper to collect a bounded snapshot in one command:

```bash
read -r MONGARS_TOKEN < secrets/api_token.txt
MONGARS_STATUS_API_TOKEN="$MONGARS_TOKEN" scripts/mongars-status.sh

# Machine-readable output
MONGARS_STATUS_API_TOKEN="$MONGARS_TOKEN" scripts/mongars-status.sh --json
```

The helper reports worker/embedding/parser readiness, active embedding identity, corpus compatibility,
compose state, task-queue pressure, and local disk pressure without echoing tokens, queries,
or task payloads.

Credential rotation is separate from destructive reset. Rotate tokens/HMACs in place without restart:

```bash
scripts/rotate-credentials.sh \
  --api-token-file ./secrets/api_token.txt \
  --approval-hmac-key-file ./secrets/approval_hmac_key.txt
```

Then restart API/worker to load new secrets:

```bash
docker compose --profile gpu up -d --force-recreate api worker
```

Destructive deployment reset is a separate command and requires explicit confirmation:

```bash
scripts/reset-deployment.sh --confirm
```

Document and Neurons resource controls are startup-validated together:

| Setting | Default | Purpose |
|---|---:|---|
| `MONGARS_MAX_DOCUMENT_REQUEST_BYTES` | 10,500,000 | Complete multipart request limit for `POST /v1/documents` |
| `MONGARS_MAX_DOCUMENT_UPLOAD_BYTES` | 10,000,000 | Declared and streamed file-byte limit |
| `MONGARS_MAX_DOCUMENT_CHARS` | 2,000,000 | Maximum normalized extracted text |
| `MONGARS_MAX_DOCUMENT_PAGES` | 500 | PDF page limit |
| `MONGARS_MAX_DOCUMENT_SECTIONS` | 10,000 | Extracted block/section limit |
| `MONGARS_MAX_DOCUMENT_ARCHIVE_ENTRIES` | 2,000 | DOCX ZIP member limit |
| `MONGARS_MAX_DOCUMENT_ARCHIVE_UNCOMPRESSED_BYTES` | 50,000,000 | DOCX aggregate expanded-byte limit |
| `MONGARS_DOCUMENT_PARSER_TIMEOUT_SECONDS` | 30 | Parser child wall-clock and bounded CPU basis |
| `MONGARS_DOCUMENT_PARSER_MEMORY_BYTES` | 536,870,912 | Parser child address-space limit |
| `MONGARS_DOCUMENT_PARSER_BASE_URL` | unset in `Settings`; `http://parser:8091` in Compose | Fixed parser origin; required by the production worker |
| `MONGARS_ALLOW_REMOTE_DOCUMENT_PARSER` | `false` | Explicit opt-in for a non-local parser origin; that origin must also use HTTPS |
| `MONGARS_DOCUMENT_STAGING_TTL_SECONDS` | 86,400 | Maximum unprocessed staging lifetime |
| `MONGARS_MAX_DOCUMENT_STAGED_OBJECTS` | 10 | Per-owner staged-object quota |
| `MONGARS_MAX_DOCUMENT_STAGED_BYTES` | 50,000,000 | Per-owner aggregate staged-byte quota |
| `MONGARS_MAX_CONCURRENT_DOCUMENT_UPLOADS` | 2 | Process-local global admission limit before multipart parsing |
| `MONGARS_MAX_CONCURRENT_DOCUMENT_UPLOADS_PER_OWNER` | 1 | Process-local per-owner admission limit |
| `MONGARS_EMBEDDING_DIMENSIONS` | 768 | Fixed pgvector/provider dimension; other values are rejected |
| `MONGARS_EMBEDDING_BATCH_SIZE` | 16 | Maximum texts sent per Ollama embedding request |
| `MONGARS_EMBEDDING_MAX_INPUT_BYTES` | 8,192 | Prepared per-input byte ceiling, including the purpose prefix |
| `MONGARS_OLLAMA_EMBEDDING_MODEL_DIGEST` | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` | Reviewed `nomic-embed-text` artifact SHA-256; alias drift is rejected |
| `MONGARS_MEMORY_CHUNK_CHARACTERS` | 32,000 | Hard character ceiling per embedding chunk, including unbroken text |
| `MONGARS_WORKER_RUNTIME_HEARTBEAT_SECONDS` | 10 | Durable worker capability publication interval |
| `MONGARS_WORKER_RUNTIME_STALE_SECONDS` | 45 | Maximum heartbeat age accepted by readiness |
| `MONGARS_RUNTIME_GIT_SHA` | `unknown` | Release Git identity published by the worker |
| `MONGARS_RUNTIME_VERSION` | `0.1.0` | Release version published by the worker |

`MONGARS_MAX_DOCUMENT_REQUEST_BYTES` must exceed the upload limit by at least 100,000 bytes for
multipart overhead. The staged-byte quota cannot be smaller than one allowed upload, and the DOCX
uncompressed limit cannot be smaller than the upload limit. In addition to these configurable
ceilings, individual DOCX members are capped at 20 MB and suspicious compression ratios above
100:1 are rejected. Staging lifetime must be at least as long as the approval lifetime.
The per-owner upload-concurrency limit cannot exceed the global limit, and the worker stale window
must exceed two heartbeat intervals. The full reviewed embedding digest is resolved and checked
before the runtime reports ready or accepts semantic work.

With `MONGARS_DOCUMENT_PARSER_BASE_URL` unset, development and test workers default to a local
`IsolatedDocumentParser` child with OS resource limits; production refuses to start without an
explicit parser origin. The supplied Compose deployment uses the local service name
`http://parser:8091` on an internal-only bridge. A hostname outside
`localhost`, `127.0.0.1`, `::1`, or `parser` is rejected unless
`MONGARS_ALLOW_REMOTE_DOCUMENT_PARSER=true`, and even then the origin must use HTTPS and contain no
credentials, path, query, or fragment. Remote parsing is therefore a deliberate TLS opt-in, never
an automatic fallback.

In the supplied production path the worker copies approved bytes over that fixed bridge to a
dedicated parser sidecar. The sidecar has no secrets, persistent/host volumes,
backend/edge/egress network, or proxy inheritance; it runs as a fixed non-root user with a
read-only filesystem, dropped capabilities, `no-new-privileges`, memory/CPU/PID ceilings, and
bounded concurrency. The worker has no listener on that bridge. Each parse then runs in a fresh
child process that receives immutable bytes and technical metadata only—never governance,
owner/task identity, a caller-supplied path, database session, or secret. Child-to-parent IPC is
length-capped JSON rather than pickle, the child environment is scrubbed, and the trusted worker
constructs provenance from the approved task and staged record. On Ubuntu the child also has
explicit CPU, address-space, open-file, and zero-output-file limits; timeout or abnormal exit
terminates the process.

Parsing and Ollama embedding happen between short task-owner transactions, while persistence, task
completion, provenance, autobiographical events, and staging deletion commit together. HTML active
content and styling ambiguity are rejected, and DOCX relationship targets are never fetched.

These controls are parser containment, not antivirus. OCR, macros, embedded media, arbitrary
archives, remote fetches, and symbolic-link/filesystem ingestion remain outside the supported
boundary. Staged bytes, extracted text, embeddings, database backups, and logs containing document
metadata should all be treated as sensitive owner data. Keep full-disk encryption and backup
retention aligned with each document's retention class.

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
threshold. Direct integration-test invocations still require `MONGARS_TEST_DATABASE_URL` and skip
when it is absent.

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
and a disposable HTTPS + authentication + required-search + approved-document deployment smoke.
The deployment smoke starts the real API and worker against deterministic inference/search
fixtures, then uploads, reviews, approves, parses, embeds, persists, and retrieves a TXT document.
The separate
supply-chain workflow adds Gitleaks plus Trivy HIGH/CRITICAL scanning and an SPDX SBOM for every
deployed image: monGARS, Caddy, PostgreSQL/pgvector, Ollama, SearXNG, and the egress proxy.

## HTTP API

| Endpoint | Purpose | Authentication |
|---|---|---|
| `GET /v1/healthz` | Process liveness | No |
| `GET /v1/readyz` | PostgreSQL, inference/search, durable worker/parser, exact embedding-space, and owner-corpus readiness | Bearer |
| `POST /v1/chat` | Local chat with owner-scoped retrieval and opt-in web search | Bearer |
| `POST /v1/memory/search` | Semantic or hybrid memory search | Bearer |
| `POST /v1/memory/documents` | Propose an approved text-note write | Bearer |
| `POST /v1/memory/reindex` | Propose an approved owner/document shadow reindex into the active embedding space | Bearer |
| `POST /v1/documents` | Stage a validated multipart upload and propose `document.ingest` | Bearer |
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

Important production controls in the supplied Compose stack include non-root app, parser, and Caddy users,
read-only root filesystems, dropped Linux capabilities, `no-new-privileges`, bounded tmpfs,
loopback-only API publication, internal-only database/inference networks, and file-mounted
secrets. Caddy is the only host-facing container; only the worker and secretless parser sidecar join
the internal parser network. The API reaches SearXNG over a separate internal network, SearXNG
reaches only the internal proxy network, and only the ACL proxy joins the ordinary search-egress
bridge. The supplied base, PostgreSQL, Ollama, SearXNG, Squid, and Caddy images are
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

Migration `0004_embedding_provenance` is an expand/compatibility phase. It copies every existing
vector into an explicitly incompatible `legacy-uninstructed-v0` space, keeps the old chunk vector
columns nullable for a rolling application transition, and requires the approved shadow reindex
before active retrieval can resume. Because the new document approval contract binds a trusted
receipt timestamp, the upgrade fails closed by cancelling any pre-upgrade nonterminal
`document.ingest` task and deleting its staged bytes; those files must be uploaded and approved
again. The downgrade restores only a preserved legacy vector and deliberately aborts if any new or
rechunked row lacks one. Treat that guard as protection against semantic corruption, not as a
routine production rollback path; restore a tested database backup instead.

## Current boundary and next slices

This release is a production-capable control-plane foundation, not the entire long-term platform.
Neurons and bounded native document ingestion now provide the semantic and corpus foundation for
the next slices: Bouche and richer autobiographical events, explicit Mimicry preferences, typed
Virtual Hands, Sommeil Paradoxal maintenance jobs, and a proposal-only Evolution Engine. OCR and
additional tools remain separate future security boundaries with their own adversarial tests.
