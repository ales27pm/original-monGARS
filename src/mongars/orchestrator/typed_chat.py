"""Typed Cortex-to-Bouche orchestration with auditable autobiographical persistence."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mongars.autobiography.contracts import GroundingStatus
from mongars.autobiography.service import AutobiographyService
from mongars.config import Settings
from mongars.dialogue import Bouche, CitationBinding, DialoguePlan
from mongars.embeddings.service import EmbeddingService
from mongars.events.repository import EventRepository
from mongars.evolution.governance import ModelGovernanceService
from mongars.ids import uuid7
from mongars.inference.base import (
    ChatMessage,
    InferenceBackend,
    InferenceError,
    InferenceResponseError,
)
from mongars.memory.repository import MemoryHit, MemoryRepository
from mongars.memory.service import MemoryService
from mongars.orchestrator.cortex import (
    WebSearchMode,
    WebSearchStatus,
    _web_grounding_violation,
    build_prompt_envelope,
    prompt_token_upper_bound,
)
from mongars.orchestrator.emotion import AffectSignal
from mongars.orchestrator.personality import PersonalitySnapshot
from mongars.orchestrator.typed_evidence import (
    canonical_prompt_bytes,
    key_prompt_evidence,
    reserve_evidence_key_budget,
)
from mongars.orchestrator.typed_journal import TypedChatJournal
from mongars.prompting import build_cortex_system_prompt
from mongars.web_search import (
    SearchResponse,
    SearxNGSearchBackend,
    WebSearchError,
    WebSearchResult,
    explicit_web_search_requested,
    search_query_from_request,
)


@dataclass(frozen=True, slots=True)
class TypedChatResult:
    trace_id: str
    session_id: UUID
    answer: str
    model: str
    memory_hits: int
    web_search_status: WebSearchStatus
    sources: tuple[WebSearchResult, ...]
    citations: tuple[CitationBinding, ...]


class TypedChatRuntime:
    """Coordinate policy, retrieval, Bouche, and typed autobiographical memory.

    Slow network and inference work runs only after the current persistence phase commits.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        inference: InferenceBackend,
        embeddings: EmbeddingService | None,
        session: AsyncSession,
        personality: PersonalitySnapshot | None = None,
        affect: AffectSignal | None = None,
        web_search: SearxNGSearchBackend | None = None,
        utc_now: Callable[[], datetime] | None = None,
        autobiography: AutobiographyService | None = None,
        legacy_events: EventRepository | None = None,
        journal: TypedChatJournal | None = None,
        bouche: Bouche | None = None,
        memory_repository: MemoryRepository | None = None,
        memory: MemoryService | None = None,
        model_resolver: Callable[[str], Awaitable[tuple[str, str | None]]] | None = None,
    ) -> None:
        if personality is not None and not isinstance(personality, PersonalitySnapshot):
            raise TypeError("personality must be a PersonalitySnapshot")
        if affect is not None and not isinstance(affect, AffectSignal):
            raise TypeError("affect must be an AffectSignal")
        self._settings = settings
        self._session = session
        self._journal = journal or TypedChatJournal(
            session=session,
            autobiography=autobiography,
            legacy_events=legacy_events,
        )
        self._bouche = bouche or Bouche(inference)
        self._memory_repository = memory_repository or MemoryRepository(session)
        if memory is not None:
            self._memory = memory
        else:
            if embeddings is None:
                raise TypeError("embeddings are required when memory is not injected")
            self._memory = MemoryService(
                settings=settings,
                repository=self._memory_repository,
                embeddings=embeddings,
            )
        self._personality = personality
        self._affect = affect
        self._web_search = web_search
        self._utc_now = utc_now or (lambda: datetime.now(UTC))
        self._model_resolver = model_resolver

    async def chat(
        self,
        *,
        owner_id: str,
        message: str,
        session_id: UUID | None,
        require_local_only: bool,
        web_search_mode: WebSearchMode = "auto",
    ) -> TypedChatResult:
        normalized = message.strip()
        if not normalized:
            raise ValueError("message must not be empty")
        if len(normalized) > self._settings.max_chat_chars:
            raise ValueError("message exceeds the configured character limit")
        if require_local_only and not self._settings.inference_is_local:
            raise PermissionError("a local inference endpoint is required")
        if web_search_mode not in {"off", "auto", "required"}:
            raise ValueError("unsupported web-search mode")

        request_time = self._utc_now()
        if request_time.tzinfo is None or request_time.utcoffset() is None:
            raise RuntimeError("Cortex UTC clock must return a timezone-aware datetime")
        request_date = request_time.astimezone(UTC).date()
        system_prompt = build_cortex_system_prompt(current_date=request_date)
        build_prompt_envelope(
            settings=self._settings,
            system_prompt=system_prompt,
            user_message=normalized,
            history=(),
            hits=(),
            web_results=(),
            personality=self._personality,
            affect=self._affect,
        )

        resolved_session_id = session_id or uuid7()
        trace_id = f"trc_{secrets.token_hex(16)}"
        model_alias, model_digest = await self._resolve_model(owner_id)
        history_bundle = await self._journal.load_history(
            owner_id=owner_id,
            session_id=resolved_session_id,
        )
        history = history_bundle.messages
        history_source_ids = history_bundle.source_ids
        await self._session.commit()

        user_turn = await self._journal.accept_user_turn(
            owner_id=owner_id,
            session_id=resolved_session_id,
            trace_id=trace_id,
            content=normalized,
            new_session=session_id is None,
        )
        await self._session.commit()

        web_search_requested = web_search_mode == "required" or (
            web_search_mode == "auto" and explicit_web_search_requested(normalized)
        )
        search_response: SearchResponse | None = None
        web_results: tuple[WebSearchResult, ...] = ()
        web_search_status: WebSearchStatus = "not_requested"
        if web_search_requested:
            if self._web_search is None:
                return await self._complete_policy_response(
                    owner_id=owner_id,
                    session_id=resolved_session_id,
                    trace_id=trace_id,
                    user_turn_id=user_turn.id,
                    system_prompt=system_prompt,
                    user_message=normalized,
                    answer="Live web search is disabled on this monGARS server.",
                    web_search_status="disabled",
                )
            query = search_query_from_request(
                normalized,
                max_chars=self._settings.web_search_max_query_chars,
            )
            try:
                search_response = await self._web_search.search(
                    query,
                    limit=self._settings.web_search_max_results,
                )
            except WebSearchError as exc:
                web_search_status = "no_results" if exc.code == "no_results" else "unavailable"
                answer = (
                    "I searched the web, but no usable results were returned, so I cannot "
                    "verify the current answer."
                    if web_search_status == "no_results"
                    else "Live web search is temporarily unavailable, so I cannot verify the "
                    "current answer."
                )
                return await self._complete_policy_response(
                    owner_id=owner_id,
                    session_id=resolved_session_id,
                    trace_id=trace_id,
                    user_turn_id=user_turn.id,
                    system_prompt=system_prompt,
                    user_message=normalized,
                    answer=answer,
                    web_search_status=web_search_status,
                )
            web_results = search_response.results
            web_search_status = "ok"
            system_prompt = build_cortex_system_prompt(
                current_date=request_date,
                web_search_completed=True,
            )

        hits: list[MemoryHit] = []
        has_documents = False
        if self._settings.memory_top_k:
            has_documents = await self._memory_repository.has_documents(owner_id=owner_id)
            await self._session.commit()
        if has_documents:
            prepared_search = await self._memory.prepare_search(normalized)
            hits = await self._memory.search_prepared(
                owner_id=owner_id,
                prepared=prepared_search,
                top_k=self._settings.memory_top_k,
                hybrid=True,
            )
            await self._session.commit()

        packing_settings = reserve_evidence_key_budget(
            self._settings,
            candidate_count=(
                len(history)
                + len(hits)
                + len(web_results)
                + int(self._personality is not None or self._affect is not None)
            ),
        )
        envelope = build_prompt_envelope(
            settings=packing_settings,
            system_prompt=system_prompt,
            user_message=normalized,
            history=history,
            hits=hits,
            web_results=web_results,
            personality=self._personality,
            affect=self._affect,
        )
        if web_search_requested and not envelope.included_web_results:
            return await self._complete_policy_response(
                owner_id=owner_id,
                session_id=resolved_session_id,
                trace_id=trace_id,
                user_turn_id=user_turn.id,
                system_prompt=system_prompt,
                user_message=normalized,
                answer=(
                    "The web results could not fit safely within the configured model context, "
                    "so I cannot verify the current answer."
                ),
                web_search_status="context_limited",
            )

        context_budget = self._settings.ollama_context_length - self._settings.ollama_num_predict
        keyed = key_prompt_evidence(
            messages=envelope.messages,
            included_history=envelope.included_history,
            included_hits=envelope.included_hits,
            included_web_results=envelope.included_web_results,
            history_source_ids=history_source_ids,
            web_retrieved_at=(
                search_response.retrieved_at if search_response is not None else None
            ),
            context_budget=context_budget,
        )
        options = {
            "temperature": 0.0 if envelope.included_web_results else 0.2,
            "num_ctx": self._settings.ollama_context_length,
            "num_predict": self._settings.ollama_num_predict,
        }
        plan = DialoguePlan(
            trace_id=trace_id,
            session_id=resolved_session_id,
            messages=keyed.messages,
            model_alias=model_alias,
            model_digest=model_digest,
            options=options,
            evidence=keyed.evidence,
            estimated_prompt_tokens=keyed.estimated_prompt_tokens,
            context_budget=context_budget,
            response_mode="answer",
            require_web_citation=web_search_requested and bool(envelope.included_web_results),
        )
        initial_grounding: GroundingStatus = (
            "partially_grounded" if keyed.evidence else "not_required"
        )
        generation = await self._journal.begin_generation(
            owner_id=owner_id,
            session_id=resolved_session_id,
            trace_id=trace_id,
            user_turn_id=user_turn.id,
            plan=plan,
            prompt_bytes=canonical_prompt_bytes(plan),
            grounding_status=initial_grounding,
        )
        await self._journal.record_context_events(
            owner_id=owner_id,
            session_id=resolved_session_id,
            trace_id=trace_id,
            memory_candidate_count=len(hits),
            evidence=keyed.evidence,
            web_search_status=web_search_status,
            web_result_count=len(web_results),
        )
        await self._session.commit()

        try:
            composed = await self._bouche.compose(plan)
            if envelope.included_web_results and _web_grounding_violation(
                answer=composed.answer,
                results=envelope.included_web_results,
            ):
                raise InferenceResponseError(
                    "Web-grounded chat response contradicted the completed search state.",
                    backend="ollama",
                    operation="chat",
                    retryable=True,
                )
        except asyncio.CancelledError:
            await self._journal.persist_failure(
                owner_id=owner_id,
                session_id=resolved_session_id,
                trace_id=trace_id,
                generation_run_id=generation.id,
                error_code="generation_cancelled",
                retryable=False,
                cancelled=True,
            )
            raise
        except Exception as exc:
            await self._journal.persist_failure(
                owner_id=owner_id,
                session_id=resolved_session_id,
                trace_id=trace_id,
                generation_run_id=generation.id,
                error_code=(
                    exc.code if isinstance(exc, InferenceError) else type(exc).__name__
                ),
                retryable=exc.retryable if isinstance(exc, InferenceError) else False,
            )
            raise

        try:
            await self._journal.complete_generation(
                owner_id=owner_id,
                session_id=resolved_session_id,
                trace_id=trace_id,
                generation_run_id=generation.id,
                composed=composed,
            )
            await self._session.commit()
        except asyncio.CancelledError:
            await self._journal.persist_failure(
                owner_id=owner_id,
                session_id=resolved_session_id,
                trace_id=trace_id,
                generation_run_id=generation.id,
                error_code="generation_cancelled",
                retryable=False,
                cancelled=True,
            )
            raise
        except Exception as exc:
            await self._journal.persist_failure(
                owner_id=owner_id,
                session_id=resolved_session_id,
                trace_id=trace_id,
                generation_run_id=generation.id,
                error_code=type(exc).__name__,
                retryable=False,
            )
            raise
        return TypedChatResult(
            trace_id=trace_id,
            session_id=resolved_session_id,
            answer=composed.answer,
            model=composed.model_alias,
            memory_hits=len(envelope.included_hits),
            web_search_status=web_search_status,
            sources=envelope.included_web_results,
            citations=composed.citations,
        )

    async def _resolve_model(self, owner_id: str) -> tuple[str, str | None]:
        if self._model_resolver is not None:
            return await self._model_resolver(owner_id)
        try:
            resolved = await ModelGovernanceService(
                self._session,
                self._settings,
            ).resolve_active_chat_model(owner_id)
        except Exception:
            await self._session.rollback()
            return (
                self._settings.ollama_chat_model,
                self._settings.model_evolution_active_chat_digest,
            )
        return resolved

    async def _complete_policy_response(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        user_turn_id: UUID,
        system_prompt: str,
        user_message: str,
        answer: str,
        web_search_status: WebSearchStatus,
    ) -> TypedChatResult:
        plan = DialoguePlan(
            trace_id=trace_id,
            session_id=session_id,
            messages=(
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_message),
            ),
            model_alias="cortex-policy",
            model_digest=None,
            options={},
            evidence=(),
            estimated_prompt_tokens=prompt_token_upper_bound(
                (
                    ChatMessage(role="system", content=system_prompt),
                    ChatMessage(role="user", content=user_message),
                )
            ),
            context_budget=self._settings.ollama_context_length
            - self._settings.ollama_num_predict,
            response_mode="abstain",
            require_web_citation=False,
            prompt_recipe_version="cortex-policy-v1",
        )
        generation = await self._journal.begin_generation(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            user_turn_id=user_turn_id,
            plan=plan,
            prompt_bytes=canonical_prompt_bytes(plan),
            grounding_status="abstained",
        )
        if web_search_status != "not_requested":
            await self._journal.record_web_event(
                owner_id=owner_id,
                session_id=session_id,
                trace_id=trace_id,
                status=web_search_status,
                result_count=0,
                evidence_keys=[],
            )
        await self._journal.complete_policy_generation(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            generation_run_id=generation.id,
            answer=answer,
        )
        await self._session.commit()
        return TypedChatResult(
            trace_id=trace_id,
            session_id=session_id,
            answer=answer,
            model="cortex-policy",
            memory_hits=0,
            web_search_status=web_search_status,
            sources=(),
            citations=(),
        )


__all__ = ["TypedChatResult", "TypedChatRuntime"]
