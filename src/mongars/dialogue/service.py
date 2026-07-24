"""Database-free Bouche service for final response generation and citation validation."""

from __future__ import annotations

import json
import re
from time import monotonic

from mongars.autobiography.contracts import GroundingStatus
from mongars.dialogue.models import CitationBinding, ComposedResponse, DialoguePlan
from mongars.inference.base import ChatMessage, InferenceBackend, InferenceResponseError

_CITATION = re.compile(r"\[([HMWP][1-9][0-9]{0,2})\]")
_THINKING = re.compile(r"</?think\b", re.IGNORECASE)
_CORRECTION_KIND = "application_citation_validation"


class Bouche:
    """Generate expression from a Cortex-approved immutable plan.

    Bouche owns no database session, tool registry, approval authority, or network origin.
    """

    def __init__(self, inference: InferenceBackend) -> None:
        self._inference = inference

    async def compose(self, plan: DialoguePlan) -> ComposedResponse:
        _validate_plan(plan)J        started = monotonic()
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
                            "brackets, or explicitly abstain when the evidence is insufficient."
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
