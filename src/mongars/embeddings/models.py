"""Typed values exchanged across the semantic-processing boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

type EmbeddingPurpose = Literal[
    "search_document",
    "search_query",
    "clustering",
    "classification",
]

type NormalizationPolicy = Literal["none", "l2"]

_PURPOSES: tuple[EmbeddingPurpose, ...] = (
    "search_document",
    "search_query",
    "clustering",
    "classification",
)


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    """Reviewed preparation policy shared by every vector in one space.

    Instructions live here, rather than at call sites, so document and query
    embeddings cannot accidentally drift into incompatible preparation policies.
    """

    version: str = "nomic-v1.5-rag-v1"
    document_instruction: str = "search_document: "
    query_instruction: str = "search_query: "
    clustering_instruction: str = "clustering: "
    classification_instruction: str = "classification: "
    truncate: bool = False

    def __post_init__(self) -> None:
        _require_identifier(self.version, field_name="profile version")
        if self.truncate:
            raise ValueError("Embedding profiles must never enable truncation.")
        for purpose in _PURPOSES:
            instruction = self.instruction_for(purpose)
            if not instruction or instruction != instruction.strip() + " ":
                raise ValueError(
                    f"The {purpose} instruction must be non-empty and end with one space."
                )

    def instruction_for(self, purpose: EmbeddingPurpose) -> str:
        """Return the reviewed instruction for an embedding purpose."""

        if purpose == "search_document":
            return self.document_instruction
        if purpose == "search_query":
            return self.query_instruction
        if purpose == "clustering":
            return self.clustering_instruction
        if purpose == "classification":
            return self.classification_instruction
        raise ValueError(f"Unsupported embedding purpose: {purpose!r}.")


@dataclass(frozen=True, slots=True)
class EmbeddingSpace:
    """Immutable, content-addressed identity for a semantic vector space."""

    provider: str
    model_alias: str
    model_digest: str
    dimension: int
    normalization_policy: NormalizationPolicy
    document_instruction: str
    query_instruction: str
    clustering_instruction: str
    classification_instruction: str
    truncate: bool
    maximum_input_bytes: int
    profile_version: str
    space_id: str = field(init=False)

    def __post_init__(self) -> None:
        _require_identifier(self.provider, field_name="provider")
        _require_identifier(self.model_alias, field_name="model alias")
        _require_sha256(self.model_digest)
        _require_identifier(self.profile_version, field_name="profile version")
        if isinstance(self.dimension, bool) or self.dimension < 1:
            raise ValueError("Embedding dimension must be positive.")
        if self.normalization_policy not in {"none", "l2"}:
            raise ValueError("Unsupported embedding normalization policy.")
        if self.truncate:
            raise ValueError("Embedding spaces must never enable truncation.")
        if isinstance(self.maximum_input_bytes, bool) or self.maximum_input_bytes < 1:
            raise ValueError("Embedding maximum input bytes must be positive.")
        profile = EmbeddingProfile(
            version=self.profile_version,
            document_instruction=self.document_instruction,
            query_instruction=self.query_instruction,
            clustering_instruction=self.clustering_instruction,
            classification_instruction=self.classification_instruction,
            truncate=self.truncate,
        )
        canonical = {
            "classification_instruction": profile.classification_instruction,
            "dimension": self.dimension,
            "document_instruction": profile.document_instruction,
            "maximum_input_bytes": self.maximum_input_bytes,
            "model_alias": self.model_alias,
            "model_digest": self.model_digest,
            "normalization_policy": self.normalization_policy,
            "profile_version": profile.version,
            "provider": self.provider,
            "query_instruction": profile.query_instruction,
            "clustering_instruction": profile.clustering_instruction,
            "truncate": profile.truncate,
        }
        payload = json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        object.__setattr__(self, "space_id", hashlib.sha256(payload).hexdigest())

    @classmethod
    def from_profile(
        cls,
        *,
        provider: str,
        model_alias: str,
        model_digest: str,
        dimension: int,
        normalization_policy: NormalizationPolicy,
        maximum_input_bytes: int,
        profile: EmbeddingProfile,
    ) -> EmbeddingSpace:
        """Construct a space from a provider artifact and reviewed profile."""

        return cls(
            provider=provider,
            model_alias=model_alias,
            model_digest=model_digest,
            dimension=dimension,
            normalization_policy=normalization_policy,
            document_instruction=profile.document_instruction,
            query_instruction=profile.query_instruction,
            clustering_instruction=profile.clustering_instruction,
            classification_instruction=profile.classification_instruction,
            truncate=profile.truncate,
            maximum_input_bytes=maximum_input_bytes,
            profile_version=profile.version,
        )


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """A validated embedding batch and non-sensitive execution metadata."""

    embeddings: tuple[tuple[float, ...], ...]
    model: str
    dimension: int
    latency_ms: float
    provider_calls: int = 1
    normalized: bool = False
    model_digest: str | None = None
    embedding_space_id: str | None = None
    purpose: EmbeddingPurpose | None = None

    @property
    def count(self) -> int:
        """Return the number of vectors without exposing the input text."""

        return len(self.embeddings)


type EmbeddingOutcome = Literal["ok", "error"]


@dataclass(frozen=True, slots=True)
class EmbeddingMetric:
    """Bounded metric record; input text is deliberately never included."""

    provider: str
    model: str
    input_count: int
    provider_calls: int
    dimension: int
    latency_ms: float
    normalized: bool
    outcome: EmbeddingOutcome
    error_code: str | None = None
    purpose: EmbeddingPurpose | None = None
    embedding_space_id: str | None = None
    input_bytes: int = 0


def validate_model_digest(value: str) -> str:
    """Validate and canonicalize a provider artifact SHA-256 digest."""

    normalized = value.removeprefix("sha256:").lower()
    _require_sha256(normalized)
    return normalized


def validate_embedding_purpose(value: object) -> EmbeddingPurpose:
    """Validate an embedding purpose at the runtime boundary."""

    if value not in _PURPOSES:
        raise ValueError(f"Unsupported embedding purpose: {value!r}.")
    return value


def _require_identifier(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"Embedding {field_name} must be a non-empty trimmed string.")


def _require_sha256(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("Embedding model digest must be a lowercase SHA-256 digest.")
