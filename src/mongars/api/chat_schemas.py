"""Chat response extensions for typed Bouche citations and NDJSON streaming."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from mongars.api.schemas import ApiModel, ChatResponse
from mongars.inference.base import JsonValue


class ChatCitation(ApiModel):
    key: str
    kind: Literal["memory", "web", "conversation", "policy"]
    source_id: str | None = None
    title: str | None = None
    url: str | None = None
    locator: dict[str, JsonValue] | None = None


class TypedChatResponse(ChatResponse):
    citations: list[ChatCitation] = Field(default_factory=list)


class ChatStreamSource(ChatCitation):
    included: bool = True


class ChatStreamStart(ApiModel):
    type: Literal["start"] = "start"
    trace_id: str
    session_id: UUID


class ChatStreamSources(ApiModel):
    type: Literal["sources"] = "sources"
    sources: list[ChatStreamSource]


class ChatStreamDelta(ApiModel):
    type: Literal["delta"] = "delta"
    text: str = Field(min_length=1)


class ChatStreamFinal(TypedChatResponse):
    type: Literal["final"] = "final"


class ChatStreamError(ApiModel):
    type: Literal["error"] = "error"
    code: str
    retryable: bool


type ChatStreamFrame = (
    ChatStreamStart
    | ChatStreamSources
    | ChatStreamDelta
    | ChatStreamFinal
    | ChatStreamError
)


__all__ = [
    "ChatCitation",
    "ChatStreamDelta",
    "ChatStreamError",
    "ChatStreamFinal",
    "ChatStreamFrame",
    "ChatStreamSource",
    "ChatStreamSources",
    "ChatStreamStart",
    "TypedChatResponse",
]
