"""Chat response extensions for typed Bouche citations."""

from __future__ import annotations

from typing import Literal

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


__all__ = ["ChatCitation", "TypedChatResponse"]
