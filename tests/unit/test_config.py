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
            ollama_context_length=512,
            ollama_num_predict=512 - CORTEX_MINIMUM_PROMPT_TOKENS + 1,
        )


def test_completion_reservation_accepts_exact_cortex_envelope_floor() -> None:
    settings = Settings(
        ollama_context_length=512,
        ollama_num_predict=512 - CORTEX_MINIMUM_PROMPT_TOKENS,
    )

    assert settings.ollama_context_length - settings.ollama_num_predict == (
        CORTEX_MINIMUM_PROMPT_TOKENS
    )
