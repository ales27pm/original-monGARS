from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mongars.embeddings.limits import (
    MAX_EMBEDDING_TEXT_BYTES,
    MAX_EMBEDDING_TEXT_CHARACTERS,
)
from mongars.embeddings.models import validate_model_digest
from mongars.prompting import CORTEX_MINIMUM_PROMPT_TOKENS

_LOCAL_OLLAMA_HOSTS = frozenset({"127.0.0.1", "localhost", "ollama"})
_LOCAL_PARSER_HOSTS = frozenset({"127.0.0.1", "localhost", "parser", "::1"})
_REVIEWED_EMBEDDING_MODEL = "nomic-embed-text"
_REVIEWED_EMBEDDING_MODEL_DIGEST = (
    "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"
)


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Validated process configuration.

    Secrets deliberately have unsafe development sentinels so importing the app remains
    convenient. Production mode refuses those values.
    """

    model_config = SettingsConfigDict(
        env_prefix="MONGARS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    owner_id: str = "local-owner"
    api_token: SecretStr = SecretStr("development-only-change-me")
    approval_hmac_key: SecretStr = SecretStr("development-only-approval-key")
    cors_origins: list[str] = Field(default_factory=list)
    trusted_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    max_request_bytes: int = Field(default=2_100_000, ge=1_024, le=25_000_000)
    max_document_request_bytes: int = Field(default=10_500_000, ge=1_024, le=25_000_000)

    database_url: str = "postgresql+psycopg://mongars:mongars@localhost:5432/mongars"
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    inference_backend: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "qwen3:4b-instruct"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_embedding_model_digest: str = _REVIEWED_EMBEDDING_MODEL_DIGEST
    ollama_think: bool = False
    ollama_context_length: int = Field(default=4096, ge=512, le=1_048_576)
    ollama_num_predict: int = Field(default=512, ge=1, le=131_072)
    inference_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    inference_health_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    # The initial migration fixes pgvector columns at 768 dimensions.
    embedding_dimensions: int = Field(default=768, ge=1, le=4096)
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    embedding_max_input_bytes: int = Field(
        default=MAX_EMBEDDING_TEXT_BYTES,
        ge=1_024,
        le=MAX_EMBEDDING_TEXT_BYTES,
    )
    allow_remote_inference: bool = False

    web_search_enabled: bool = False
    web_search_base_url: str = "http://127.0.0.1:8080"
    web_search_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    web_search_max_query_chars: int = Field(default=500, ge=1, le=2_000)
    web_search_max_results: int = Field(default=5, ge=1, le=50)
    web_search_max_response_bytes: int = Field(
        default=1_000_000,
        ge=1_024,
        le=10_000_000,
    )

    max_chat_chars: int = Field(default=32_000, ge=256, le=1_000_000)
    max_document_chars: int = Field(default=2_000_000, ge=1_000, le=20_000_000)
    max_document_upload_bytes: int = Field(default=10_000_000, ge=1_024, le=20_000_000)
    max_document_pages: int = Field(default=500, ge=1, le=10_000)
    max_document_sections: int = Field(default=10_000, ge=1, le=100_000)
    max_document_archive_entries: int = Field(default=2_000, ge=1, le=20_000)
    max_document_archive_uncompressed_bytes: int = Field(
        default=50_000_000,
        ge=1_024,
        le=250_000_000,
    )
    document_parser_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    document_parser_memory_bytes: int = Field(
        default=536_870_912,
        ge=134_217_728,
        le=2_147_483_648,
    )
    document_parser_base_url: str | None = None
    allow_remote_document_parser: bool = False
    document_staging_ttl_seconds: int = Field(default=86_400, ge=300, le=604_800)
    max_document_staged_objects: int = Field(default=10, ge=1, le=100)
    max_document_staged_bytes: int = Field(
        default=50_000_000,
        ge=1_024,
        le=500_000_000,
    )
    max_concurrent_document_uploads: int = Field(default=2, ge=1, le=32)
    max_concurrent_document_uploads_per_owner: int = Field(default=1, ge=1, le=8)
    memory_chunk_tokens: int = Field(default=800, ge=32, le=4096)
    memory_chunk_overlap_tokens: int = Field(default=100, ge=0, le=1024)
    memory_chunk_characters: int = Field(
        default=MAX_EMBEDDING_TEXT_CHARACTERS,
        ge=256,
        le=MAX_EMBEDDING_TEXT_CHARACTERS,
    )
    memory_top_k: int = Field(default=8, ge=0, le=50)
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_lease_seconds: int = Field(default=120, ge=10, le=3600)
    worker_runtime_heartbeat_seconds: float = Field(default=10.0, gt=0, le=300)
    worker_runtime_stale_seconds: int = Field(default=45, ge=10, le=900)
    runtime_git_sha: str = Field(default="unknown", min_length=1, max_length=64)
    runtime_version: str = Field(default="0.1.0", min_length=1, max_length=100)
    retention_sweep_seconds: int = Field(default=300, ge=10, le=86_400)
    approval_ttl_seconds: int = Field(default=900, ge=30, le=86_400)

    @property
    def inference_is_local(self) -> bool:
        """Return whether the configured inference endpoint is in the local trust boundary."""

        return (
            self.inference_backend == "ollama"
            and urlparse(self.ollama_base_url).hostname in _LOCAL_OLLAMA_HOSTS
        )

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        return normalized

    @field_validator("cors_origins")
    @classmethod
    def reject_wildcard_cors(cls, value: list[str]) -> list[str]:
        if "*" in value:
            raise ValueError("wildcard CORS origins are not permitted")
        return value

    @field_validator("document_parser_base_url")
    @classmethod
    def validate_document_parser_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip():
            raise ValueError("document_parser_base_url must be a non-empty trimmed URL")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("document_parser_base_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("document_parser_base_url must not include credentials")
        if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
            raise ValueError("document_parser_base_url must be an origin without path or query")
        return value.rstrip("/")

    @field_validator("ollama_embedding_model_digest")
    @classmethod
    def validate_embedding_model_digest(cls, value: str) -> str:
        try:
            return validate_model_digest(value)
        except ValueError as exc:
            raise ValueError("ollama_embedding_model_digest must be a SHA-256 digest") from exc

    @model_validator(mode="after")
    def validate_security_boundaries(self) -> Settings:
        if self.inference_backend != "ollama":
            raise ValueError("only the local Ollama backend is enabled in this release")

        parsed = urlparse(self.ollama_base_url)
        hostname = parsed.hostname
        if parsed.scheme not in {"http", "https"} or hostname is None:
            raise ValueError("ollama_base_url must be an absolute HTTP(S) URL")
        if not self.allow_remote_inference and not self.inference_is_local:
            raise ValueError("remote inference is disabled")
        if not self.inference_is_local and parsed.scheme != "https":
            raise ValueError("remote inference requires TLS")

        if self.environment is Environment.PRODUCTION:
            if self.api_token.get_secret_value() == "development-only-change-me":
                raise ValueError("MONGARS_API_TOKEN must be changed in production")
            if self.approval_hmac_key.get_secret_value() == "development-only-approval-key":
                raise ValueError("MONGARS_APPROVAL_HMAC_KEY must be changed in production")
        if self.memory_chunk_overlap_tokens >= self.memory_chunk_tokens:
            raise ValueError("memory chunk overlap must be smaller than chunk size")
        if self.ollama_num_predict >= self.ollama_context_length:
            raise ValueError("ollama_num_predict must be smaller than ollama_context_length")
        prompt_tokens = self.ollama_context_length - self.ollama_num_predict
        if prompt_tokens < CORTEX_MINIMUM_PROMPT_TOKENS:
            raise ValueError(
                "ollama_context_length - ollama_num_predict must leave at least "
                f"{CORTEX_MINIMUM_PROMPT_TOKENS} tokens for the Cortex prompt envelope"
            )
        if self.embedding_dimensions != 768:
            raise ValueError("the current pgvector schema requires 768-dimensional embeddings")
        if self.ollama_embedding_model != _REVIEWED_EMBEDDING_MODEL:
            raise ValueError("this release requires the reviewed nomic-embed-text embedding model")
        if self.document_parser_base_url is not None:
            parser_url = urlparse(self.document_parser_base_url)
            parser_is_local = parser_url.hostname in _LOCAL_PARSER_HOSTS
            if not parser_is_local and not self.allow_remote_document_parser:
                raise ValueError("remote document parsing is disabled")
            if not parser_is_local and parser_url.scheme != "https":
                raise ValueError("remote document parsing requires TLS")
        if self.max_document_archive_uncompressed_bytes < self.max_document_upload_bytes:
            raise ValueError(
                "max_document_archive_uncompressed_bytes cannot be smaller than the upload limit"
            )
        if self.max_document_staged_bytes < self.max_document_upload_bytes:
            raise ValueError("max_document_staged_bytes cannot be smaller than the upload limit")
        if self.max_concurrent_document_uploads_per_owner > self.max_concurrent_document_uploads:
            raise ValueError("per-owner document upload concurrency cannot exceed the global limit")
        if self.worker_runtime_stale_seconds <= self.worker_runtime_heartbeat_seconds * 2:
            raise ValueError("worker_runtime_stale_seconds must exceed two heartbeat intervals")
        if self.document_staging_ttl_seconds < self.approval_ttl_seconds:
            raise ValueError(
                "document_staging_ttl_seconds cannot be shorter than approval_ttl_seconds"
            )
        if self.max_document_request_bytes < self.max_document_upload_bytes + 100_000:
            raise ValueError(
                "max_document_request_bytes must exceed max_document_upload_bytes by at least "
                "100000"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
