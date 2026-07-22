from __future__ import annotations

import pytest
from pydantic import ValidationError

from mongars.config import Environment, Settings
from mongars.prompting import CORTEX_MINIMUM_PROMPT_TOKENS


def test_production_rejects_default_api_secret() -> None:
    with pytest.raises(ValidationError, match="MONGARS_API_TOKEN must be changed"):
        Settings(
            environment=Environment.PRODUCTION,
            api_token="development-only-change-me",  # noqa: S106 - sentinel under test
            approval_hmac_key="production-hmac-key",
        )


def test_production_rejects_default_approval_secret() -> None:
    with pytest.raises(ValidationError, match="MONGARS_APPROVAL_HMAC_KEY must be changed"):
        Settings(
            environment=Environment.PRODUCTION,
            api_token="production-api-key",  # noqa: S106 - test-only value
            approval_hmac_key="development-only-approval-key",
        )


def test_production_accepts_replaced_secrets() -> None:
    settings = Settings(
        environment=Environment.PRODUCTION,
        api_token="production-api-key",  # noqa: S106 - test-only value
        approval_hmac_key="production-hmac-key",
    )

    assert settings.environment is Environment.PRODUCTION


def test_remote_inference_is_rejected_when_disabled_even_with_tls() -> None:
    with pytest.raises(ValidationError, match="remote inference is disabled"):
        Settings(
            ollama_base_url="https://gpu-box.example:11434",
            allow_remote_inference=False,
        )


def test_remote_inference_requires_tls_when_enabled() -> None:
    with pytest.raises(ValidationError, match="remote inference requires TLS"):
        Settings(
            ollama_base_url="http://gpu-box.example:11434",
            allow_remote_inference=True,
        )


def test_remote_inference_accepts_explicit_tls_endpoint() -> None:
    settings = Settings(
        ollama_base_url="https://gpu-box.example:11434",
        allow_remote_inference=True,
    )

    assert settings.ollama_base_url == "https://gpu-box.example:11434"
    assert settings.inference_is_local is False


def test_local_ollama_endpoint_can_use_http_without_remote_opt_in() -> None:
    settings = Settings(
        ollama_base_url="http://ollama:11434",
        allow_remote_inference=False,
    )

    assert settings.ollama_base_url == "http://ollama:11434"
    assert settings.inference_is_local is True


def test_embedding_dimension_parses_environment_strings() -> None:
    assert Settings(embedding_dimensions="768").embedding_dimensions == 768  # type: ignore[arg-type]


def test_embedding_dimension_must_match_the_migration() -> None:
    with pytest.raises(ValidationError, match="requires 768-dimensional embeddings"):
        Settings(embedding_dimensions=1024)


def test_embedding_model_is_fixed_to_the_reviewed_release_identity() -> None:
    with pytest.raises(ValidationError, match="requires the reviewed nomic-embed-text"):
        Settings(ollama_embedding_model="another-embedding-model")

    assert Settings(ollama_embedding_model="nomic-embed-text").ollama_embedding_model == (
        "nomic-embed-text"
    )


def test_memory_chunk_character_ceiling_cannot_exceed_embedding_boundary() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 32000"):
        Settings(memory_chunk_characters=32_001)


def test_document_parser_origin_is_normalized_and_rejects_credentials_or_paths() -> None:
    assert Settings(document_parser_base_url="http://parser:8091/").document_parser_base_url == (
        "http://parser:8091"
    )
    for value in ("parser:8091", "http://user:pass@parser:8091", "http://parser:8091/api"):
        with pytest.raises(ValidationError, match="document_parser_base_url"):
            Settings(document_parser_base_url=value)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "max_document_upload_bytes": 2_000_000,
                "max_document_archive_uncompressed_bytes": 1_999_999,
            },
            "archive_uncompressed_bytes cannot be smaller",
        ),
        (
            {
                "max_document_upload_bytes": 2_000_000,
                "max_document_staged_bytes": 1_999_999,
            },
            "max_document_staged_bytes cannot be smaller",
        ),
        (
            {
                "max_document_upload_bytes": 2_000_000,
                "max_document_request_bytes": 2_099_999,
            },
            "must exceed max_document_upload_bytes",
        ),
        (
            {
                "document_staging_ttl_seconds": 899,
                "approval_ttl_seconds": 900,
            },
            "cannot be shorter than approval_ttl_seconds",
        ),
    ],
)
def test_document_resource_limits_have_consistent_envelopes(
    overrides: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(**overrides)  # type: ignore[arg-type]


def test_completion_reservation_must_leave_room_for_the_prompt() -> None:
    with pytest.raises(
        ValidationError,
        match="ollama_num_predict must be smaller than ollama_context_length",
    ):
        Settings(ollama_context_length=512, ollama_num_predict=512)


def test_completion_reservation_must_leave_room_for_cortex_envelope() -> None:
    with pytest.raises(
        ValidationError,
        match=(
            "ollama_context_length - ollama_num_predict must leave at least "
            rf"{CORTEX_MINIMUM_PROMPT_TOKENS} tokens"
        ),
    ):
        Settings(
            ollama_context_length=CORTEX_MINIMUM_PROMPT_TOKENS + 128,
            ollama_num_predict=129,
        )


def test_completion_reservation_accepts_exact_cortex_envelope_floor() -> None:
    settings = Settings(
        ollama_context_length=CORTEX_MINIMUM_PROMPT_TOKENS + 128,
        ollama_num_predict=128,
    )

    assert settings.ollama_context_length - settings.ollama_num_predict == (
        CORTEX_MINIMUM_PROMPT_TOKENS
    )
