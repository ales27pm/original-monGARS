# Mobile Application Guidance

## Scope

This file governs `apps/mobile/`. It refines the repository-root
[AGENTS.md](../../AGENTS.md).

## Role in the System

`@mongars/mobile` is an independent Expo/React Native client for chat, memory,
document upload, tasks, settings, and personality feedback. It manually
implements backend HTTP and NDJSON contracts; there is no generated client.

## Key Files and Entry Points

- `app/_layout.tsx`: Expo Router root and provider mounting.
- `app/(tabs)/`: chat, memory, task, and settings screens.
- `providers/mongars-provider.tsx`: restored API base URL/token state and client
  lifecycle.
- `lib/api/client.ts`: core HTTP client, auth handling, task/memory/document/chat
  methods, and streamed chat contract.
- `lib/api/adaptation.ts`: feedback/profile client.
- `lib/api/ndjson.ts`: incremental NDJSON parser.
- `lib/api-origin.ts`, `lib/api-base-url.ts`, `lib/api-token.ts`: normalized
  origin, persistence, credential, and secure-transport policy.
- `lib/document-upload.ts`: filename/MIME/size/metadata preflight.
- `hooks/use-mongars-api.ts`, `hooks/use-adaptation.ts`: abortable request state.
- `types/mongars-api.ts`, `types/adaptation.ts`: manually maintained wire types.
- `apps/mobile/tests/*.test.cjs`: Node contract tests that inspect and exercise
  these
  boundaries.
- `package.json`, `app.json`, `eas.json`, `tsconfig.json`: tool/build authority.

## Incoming and Outgoing Dependencies

Screens and hooks call the provider/client. The client calls the Python API
routes under `/v1`, including health/readiness, chat/stream, memory, documents,
tasks, and adaptation. Local persistence stores API configuration and
credentials through the dedicated modules. Expo Router and platform storage/
network APIs are outgoing dependencies.

## Data and Control Flow

On startup, `MongarsProvider` restores the base URL and token, normalizes origin
and transport security, rejects incompatible configuration, and constructs
`MongarsClient`. Hooks create abortable requests and expose loading/error/result
state. A 401 from an authenticated request clears the stored token. Streamed
chat verifies `application/x-ndjson`, parses bounded frames incrementally, and
enforces start/sources/delta/final ordering and final-answer consistency.

## Public Contracts

- Bearer authentication and 401 credential invalidation.
- `/v1/chat` and `/v1/chat/stream` request/response/frame types.
- Memory search and document/staged-upload/task result shapes.
- Adaptation feedback, profile, proposal, and revision shapes.
- Persisted API origin/base URL and token behavior.

## Local Invariants

- Credentials may be sent only over HTTPS or approved loopback development
  transport. Do not treat arbitrary LAN HTTP as secure.
- Changing API origin clears or rejects credentials associated with the prior
  origin; do not silently send a token to a different host.
- Normalize and validate base URLs in the dedicated modules, not independently
  in screens.
- Clear the credential on authenticated 401 responses without exposing it in
  errors or logs.
- Preserve abort/cancellation on unmount and request replacement.
- NDJSON parsing is incremental and bounded; do not split only on each network
  chunk or buffer an unlimited response.
- Stream frames must be ordered, uniquely terminal, and consistent with the
  assembled answer.
- Document preflight preserves the backend filename, MIME, byte, and metadata
  constraints. Client validation does not replace server validation.
- Keep wire types synchronized manually with Python schemas; TypeScript
  compilation alone does not prove runtime compatibility.

## Coordinated Changes

- Backend route/schema: `types/`, matching client method, hook/screen, contract
  test, and Python API test.
- Stream frame: `lib/api/client.ts`, `lib/api/ndjson.ts`, types, chat UI state,
  mobile stream tests, Python stream schemas/serializer/tests.
- Auth/origin: provider, token/base-url/origin modules, client, settings UI,
  backend auth/CORS/trusted-host behavior, and credential tests.
- Upload format/limit: document helper, picker/UI, backend route/registry/config,
  and both contract suites.
- Feedback/profile: adaptation client/types/hooks/controls/settings,
  backend adaptation/task contracts, and tests.
- Navigation/theme asset: route layouts, app configuration, platform assets,
  and web/Android/iOS behavior.

## Safe Editing Rules

Put transport logic in `lib/api`, persistent configuration in its dedicated
modules/provider, request lifecycle in hooks, and presentation in screens/
components. Do not make a screen bypass `MongarsClient` for an authenticated
endpoint. Preserve established Expo Router and theme patterns.

## Validation

Working directory: `apps/mobile`.

```sh
npm ci
npm run lint
npm run typecheck
npm test
```

For interactive runs:

```sh
npm run start:dev-client
npm run android
npm run ios
npm run web
```

EAS development builds are exposed as `npm run build:development` and
`npm run build:development:ios`.

## Common Failure Modes

- Sending a stored token to a changed or nonsecure API origin.
- Treating a fetch chunk as one complete NDJSON frame.
- Updating TypeScript types without runtime validation or backend changes.
- Swallowing an abort as a user-visible API error, or failing to abort stale
  hooks.
- Accepting a file in the mobile picker that backend ingestion rejects.
- Adding a route file without the intended Expo Router layout/tab relationship.

## Parent and Child Guidance

Parent: [repository AGENTS.md](../../AGENTS.md). No child `AGENTS.md` is needed;
tests and app code use the same package scripts and wire-contract rules.
