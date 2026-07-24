"""Database-free Bouche service for final response generation and citation validation."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from time import monotonic

from mongars.autobiography.contracts import GroundingStatus
from mongars.dialogue.models import (
    BoucheStreamDelta,
    BoucheStreamEvent,
    BoucheStreamFinal,
    CitationBinding,
    ComposedResponse,
    DialoguePlan,
)
from mongars.inference.base import (
    ChatMessage,
    ChatStreamChunk,
    InferenceBackend,
    InferenceResponseError,
    StreamingInferenceBackend,
)

_CITATION = re.compile(r"\[([HMWP][1-9][0-9]{0,2})\]")
_THINKING = re.compile(r"</?think\b", re.IGNORECASE)
_CORRECTION_KIND = "application_citation_validation"
_STREAM_FALLBACK_CHARS = 256
_STREAM_GUARD_CHARS = 16


class Bouche:
    """Generate expression from a Cortex-approved immutable plan.

    Bouche owns no database session, tool registry, approval authority, or network origin.
    """

    def __init__(self, inference: InferenceBackend) -> None:
        self._inference = inference

    async def compose(self, plan: DialoguePlan) -> ComposedResponse:
        _validate_plan(plan)
        started = monotonic()
        response = await self._inference.chat(
            plan.messages,
            model=plan.model_alias,
            options=plan.options,
        )
        answer = _validate_answer(response.content)
        citations = _citations(answer, plan)

        if plan.require_web_citation and not any(binding.kind == "web" for binding in citations):
            retry_messages = _append_tool_message(
                plan.messages,
                json.dumps(
                    {
                        "kind": _CORRECTION_KIND,
                        "instruction": (
                            "Answer again. Cite at least one supplied web evidence key in square "
                            "brackets. If the evidence is insufficient, state that limitation while "
                            "citing the evidence that supports it."
                        ),
                        "allowed_web_keys": [
                            item.key for item in plan.evidence if item.kind == "web" and item.included
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            response = await self._inference.chat(
                retry_messages,
                model=plan.model_alias,
                options=plan.options,
            )
            answer = _validate_answer(response.content)
            citations = _citations(answer, plan)
            if not any(binding.kind == "web" for binding in citations):
                raise InferenceResponseError(
                    "Bouche response did not cite required web evidence.",
                    backend="ollama",
                    operation="chat",
                    retryable=True,
                )

        grounding_status = _grounding_status(plan, citations)
        return ComposedResponse(
            answer=answer,
            model_alias=response.model,
            model_digest=plan.model_digest,
            finish_reason=response.done_reason,
            citations=citations,
            grounding_status=grounding_status,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=(monotonic() - started) * 1_000,
        )

    async def stream(self, plan: DialoguePlan) -> AsyncIterator[BoucheStreamEvent]:
        """Stream safe response fragments and terminate with one validated result.

        Required-web responses retain the existing bounded correction behavior by using the
        verified non-streaming path and then chunking the validated answer. Other plans use
        native backend streaming when available. If final validation fails, no final event is
        emitted and the caller must discard the draft fragments.
        """

        _validate_plan(plan)
        if plan.require_web_citation or not isinstance(
            self._inference,
            StreamingInferenceBackend,
        ):
            composed = await self.compose(plan)
            for text in _chunk_text(composed.answer):
                yield BoucheStreamDelta(text=text)
            yield BoucheStreamFinal(response=composed)
            return

        started = monotonic()
        guard = _StreamingTextGuard()
        parts: list[str] = []
        terminal: ChatStreamChunk | None = None
        established_model: str | None = None

        async for chunk in self._inference.stream_chat(
            plan.messages,
            model=plan.model_alias,
            options=plan.options,
        ):
            if established_model is None:
                established_model = chunk.model
            elif chunk.model != established_model:
                raise InferenceResponseError(
                    "Bouche stream changed models during one response.",
                    backend="ollama",
                    operation="chat_stream",
                    retryable=False,
                )
            if terminal is not None:
                raise InferenceResponseError(
                    "Bouche stream emitted data after completion.",
                    backend="ollama",
                    operation="chat_stream",
                    retryable=False,
                )
            if chunk.content:
                safe = guard.feed(chunk.content)
                if safe:
                    parts.append(safe)
                    yield BoucheStreamDelta(text=safe)
            if chunk.done:
                terminal = chunk

        tail = guard.finish()
        if tail:
            parts.append(tail)
            yield BoucheStreamDelta(text=tail)
        if terminal is None:
            raise InferenceResponseError(
                "Bouche stream ended without a terminal response.",
                backend="ollama",
                operation="chat_stream",
                retryable=True,
            )

        answer = _validate_answer("".join(parts))
        citations = _citations(answer, plan)
        composed = ComposedResponse(
            answer=answer,
            model_alias=terminal.model,
            model_digest=plan.model_digest,
            finish_reason=terminal.done_reason,
            citations=citations,
            grounding_status=_grounding_status(plan, citations),
            prompt_tokens=terminal.prompt_tokens,
            completion_tokens=terminal.completion_tokens,
            latency_ms=(monotonic() - started) * 1_000,
        )
        yield BoucheStreamFinal(response=composed)


class _StreamingTextGuard:
    """Normalize stream edges and retain enough suffix to block split hidden markers."""

    def __init__(self) -> None:
        self._pending = ""
        self._started = False

    def feed(self, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("streamed Bouche content must be a string")
        self._pending += value
        if not self._started:
            self._pending = self._pending.lstrip()
            self._started = bool(self._pending)
        self._reject_hidden_reasoning()

        guarded_limit = max(0, len(self._pending) - _STREAM_GUARD_CHARS)
        emit_length = min(guarded_limit, _trailing_whitespace_start(self._pending))
        emitted = self._pending[:emit_length]
        self._pending = self._pending[emit_length:]
        return emitted

    def finish(self) -> str:
        if not self._started:
            self._pending = self._pending.lstrip()
        self._reject_hidden_reasoning()
        emitted = self._pending.rstrip()
        self._pending = ""
        return emitted

    def _reject_hidden_reasoning(self) -> None:
        if _THINKING.search(self._pending) is not None:
            raise InferenceResponseError(
                "Bouche response contains a hidden-reasoning marker.",
                backend="ollama",
                operation="chat_stream",
                retryable=False,
            )


def _trailing_whitespace_start(value: str) -> int:
    index = len(value)
    while index and value[index - 1].isspace():
        index -= 1
    return index


def _chunk_text(value: str) -> tuple[str, ...]:
    return tuple(
        value[index : index + _STREAM_FALLBACK_CHARS]
        for index in range(0, len(value), _STREAM_FALLBACK_CHARS)
    )


def _validate_plan(plan: DialoguePlan) -> None:
    if not plan.trace_id or len(plan.trace_id) > 128:
        raise ValueError("dialogue trace_id must be non-empty and at most 128 characters")
    if not plan.messages or plan.messages[-1].role != "user":
        raise ValueError("dialogue messages must end with the current user message")
    if not plan.model_alias.strip() or plan.model_alias != plan.model_alias.strip():
        raise ValueError("dialogue model_alias must be a trimmed non-empty string")
    if plan.context_budget <= 0 or plan.estimated_prompt_tokens < 0:
        raise ValueError("dialogue prompt budget metadata is invalid")
    if plan.estimated_prompt_tokens > plan.context_budget:
        raise ValueError("dialogue prompt exceeds the approved context budget")
    keys = [item.key for item in plan.evidence]
    if len(keys) != len(set(keys)):
        raise ValueError("dialogue evidence keys must be unique")
    if plan.require_web_citation and not any(
        item.kind == "web" and item.included for item in plan.evidence
    ):
        raise ValueError("required web citation needs included web evidence")


def _validate_answer(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise InferenceResponseError(
            "Bouche response is empty.",
            backend="ollama",
            operation="chat",
            retryable=True,
        )
    if _THINKING.search(normalized) is not None:
        raise InferenceResponseError(
            "Bouche response contains a hidden-reasoning marker.",
            backend="ollama",
            operation="chat",
            retryable=False,
        )
    return normalized


def _citations(answer: str, plan: DialoguePlan) -> tuple[CitationBinding, ...]:
    by_key = {item.key: item for item in plan.evidence if item.included}
    ordered: list[CitationBinding] = []
    seen: set[str] = set()
    for key in _CITATION.findall(answer):
        item = by_key.get(key)
        if item is None:
            raise InferenceResponseError(
                f"Bouche response cited unknown evidence key {key}.",
                backend="ollama",
                operation="chat",
                retryable=True,
            )
        if key in seen:
            continue
        seen.add(key)
        ordered.append(
            CitationBinding(
                key=key,
                kind=item.kind,
                source_id=item.source_id,
                title=item.title,
                source_uri=item.source_uri,
                locator=item.locator,
            )
        )
    return tuple(ordered)


def _grounding_status(
    plan: DialoguePlan,
    citations: tuple[CitationBinding, ...],
) -> GroundingStatus:
    if plan.response_mode == "abstain":
        return "abstained"
    included = tuple(item for item in plan.evidence if item.included)
    if not included:
        return "not_required"
    if citations:
        return "grounded"
    return "partially_grounded"


def _append_tool_message(
    messages: tuple[ChatMessage, ...],
    content: str,
) -> tuple[ChatMessage, ...]:
    if not messages or messages[-1].role != "user":
        raise ValueError("dialogue messages must end with the current user message")
    return (*messages[:-1], ChatMessage(role="tool", content=content), messages[-1])


__all__ = ["Bouche"]
