# ADR 0011: Authenticated NDJSON chat streaming

## Status

Proposed on `agent/bouche-ndjson-streaming-clients`, stacked on the typed Cortex/Bouche integration.

## Context

The non-streaming `/v1/chat` path now records accepted user turns, generation runs, exact evidence snapshots, final assistant turns, failures, and cancellations through typed Autobiographical Memory. The iPhone client still waits for the complete model response before displaying any answer. Adding transport streaming must not weaken the existing policy, evidence, citation, privacy, transaction, or cancellation guarantees.

A streamed draft is not a committed assistant turn. The model may emit an invalid evidence key, a hidden-reasoning marker, or a transport failure after earlier text fragments. Clients therefore cannot treat received deltas as durable or authoritative until the server emits a validated final frame.

## Decision

Add `POST /v1/chat/stream` using `application/x-ndjson` over the existing authenticated HTTPS boundary. The frame sequence is:

1. `start` with server-generated trace and session identifiers;
2. `sources` with the exact included H/M/W/P evidence catalog;
3. zero or more `delta` frames containing user-visible answer text;
4. exactly one `final` frame containing the fully validated answer and trusted citation metadata, or one bounded `error` frame.

The endpoint reuses `TypedChatRuntime`. A `StreamingBouche` adapter is injected through the existing Bouche seam after the generation start and evidence snapshots have committed. Database transactions remain closed while Ollama streams. On client disconnect, the producer task is cancelled; `TypedChatRuntime` records `generation_cancelled` and does not commit a final assistant turn.

Ollama streaming is normalized behind an optional `StreamingInferenceBackend` protocol. The adapter validates every NDJSON object, enforces one model identity and one terminal chunk, bounds line and error-body sizes, rejects truncated completions, disables environment proxy inheritance, and translates backend errors into stable application errors.

Bouche withholds a small suffix while streaming so a hidden-reasoning marker split across backend chunks cannot reach the client. Final answer and citation validation still run over the complete accumulated text. If validation fails, the stream emits no final frame. Expo discards its transient draft on every error or cancellation.

Required-web requests deliberately use the existing bounded non-streaming citation-correction path before the answer is chunked to the client. This prevents an invalid first web-grounded draft from being displayed. Native token streaming remains enabled for local-memory and ordinary conversational paths.

The Expo client uses `expo/fetch`, an incremental UTF-8 NDJSON decoder, strict frame validation, the existing origin-bound SecureStore credential policy, and AbortController cancellation. Only server-returned final citation objects may create source chips or external links. Model-created URLs in answer text remain untrusted text.

## Consequences

- Time-to-first-visible-text improves without moving inference or personal memory off the station.
- Accepted user turns survive interrupted generations, while invalid assistant drafts do not enter conversation history.
- Final citations remain application-validated and source metadata remains server-derived.
- Required-web requests may begin displaying later than ordinary requests because validation and one corrective retry occur before chunking.
- The existing non-streaming endpoint remains backward compatible.
- Full validation requires Python unit tests, PostgreSQL integration tests, Expo lint/type/tests, an authenticated HTTPS streaming smoke, physical-iPhone cancellation testing, and confirmation that the reverse proxy does not buffer NDJSON.
