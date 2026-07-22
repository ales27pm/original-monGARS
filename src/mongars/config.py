from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mongars.prompting import CORTEX_MINIMUM_PROMPT_TOKENS

_LOCAL_OLLAMA_HOSTS = frozenset({"127.0.0.1", "localhost", "ollama"})


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

    database_url: str = "postgresql+psycopg://mongars:mongars@localhost:5432/mongars"
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    inference_backend: str = "ollama"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "qwen3:4b"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_think: bool = False
    ollama_context_length: int = Field(default=4096, ge=512, le=1_048_576)
    ollama_num_predict: int = Field(default=512, ge=1, le=131_072)
    inference_timeout_seconds: float = Field(default=90.0, gt=0, le=600)
    inference_health_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    # The initial migration fixes pgvector columns at 768 dimensions.
    embedding_dimensions: int = Field(default=768, ge=1, le=4096)
    embedding_batch_size: int = Field(default=16, ge=1, le=128)
    allow_remote_inference: bool = False

    max_chat_chars: int = Field(default=32_000, ge=256, le=1_000_000)
    max_document_chars: int = Field(default=2_000_000, ge=1_000, le=20_000_000)
    memory_chunk_tokens: int = Field(default=800, ge=32, le=4096)
    memory_chunk_overlap_tokens: int = Field(default=100, ge=0, le=1024)
    memory_top_k: int = Field(default=8, ge=0, le=50)
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_lease_seconds: int = Field(default=120, ge=10, le=3600)
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
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
