# ADR 0011: Authenticated Bouche NDJSON streaming

## Status

Proposed on `agent/bouche-streaming-clients`, stacked on the typed Cortex/Bouche integration.

## Context

The typed chat runtime persists the accepted user turn, exact evidence snapshots, prompt identity, generation lifecycle, and final assistant turn. Its existing `/v1/chat` endpoint returns only after local inference and validation complete. Mobile users need visible progress and cancellation without weakening Bouche's citation validation or the autobiographical transaction model.

Ollama emits chat streams as newline-delimited JSON and may emit hidden reasoning separately or inside marker-delimited content. Bouche can also invoke inference a second time when a draft fails citation validation. A transport that blindly appends tokens would expose hidden reasoning, retain a rejected first draft, or present model-created source metadata as trusted.

## Decision

Add an authenticated `POST /v1/chat/stream` endpoint using `application/x-ndjson` and protocol identifier `mongars-chat-ndjson-v1`.

The stream emits bounded frames:

- `start`: transport identity;
- `attempt`: one Bouche inference attempt;
- `reset`: discard provisional text before a validation retry;
- `delta`: provisional user-visible text for the active attempt;
- `final`: the authoritative typed chat response after Bouche validation and durable completion;
- `error`: a bounded code instructing the client to discard provisional text.

The default Ollama adapter suppresses one leading `<think>...</think>` trace across arbitrary transport boundaries, ignores Ollama's separate `thinking` field, rejects residual or unfinished markers, bounds each NDJSON frame, and produces a normalized terminal `ChatResponse`.

A request-scoped inference observer exposes deltas while preserving the existing `InferenceBackend.chat` contract. Cortex policy, Hippocampus retrieval, Bouche validation, and Autobiographical Memory remain unchanged. If the client disconnects, the response generator cancels the typed runtime; the runtime records `generation_cancelled` and commits no final assistant turn.

Expo consumes the stream with `expo/fetch`, an abort controller, `ReadableStream.getReader()`, a bounded incremental decoder, strict frame sequencing, and final-response verification. Provisional text is cleared on `reset` and on errors. Source links and locators are rendered only from the server-validated `final.response.citations` metadata.

## Consequences

- The non-streaming endpoint remains backward compatible.
- Provisional text is explicitly non-authoritative until the final frame arrives.
- Bouche retries are observable without mixing rejected and accepted drafts.
- Hidden reasoning is neither streamed nor persisted.
- Cancellation uses the same typed lifecycle and short transaction boundaries as ordinary failures.
- Production validation must include certificate-verified HTTPS streaming and physical-iPhone cancellation.
