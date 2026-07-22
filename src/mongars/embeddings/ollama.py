"""Dedicated Ollama provider for the Neurons embedding boundary."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from time import monotonic
from typing import Any
from urllib.parse import urlsplit

import httpx

from .errors import (
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingContextError,
    EmbeddingDimensionError,
    EmbeddingHTTPError,
    EmbeddingInputError,
    EmbeddingModelDigestMismatchError,
    EmbeddingModelMismatchError,
    EmbeddingResponseError,
    EmbeddingTimeoutError,
)
from .limits import MAX_EMBEDDING_TEXT_BYTES
from .models import EmbeddingBatch, validate_model_digest

_PROVIDER = "ollama"
_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_DIMENSION = 768
_DEFAULT_MAX_RESPONSE_BYTES = 8_000_000
_MAX_RESPONSE_BYTES = 64_000_000
_STREAM_CHUNK_BYTES = 65_536
_SHA256_PATTERN = re.compile(r"(?:sha256:)?[0-9a-fA-F]{64}")


class OllamaEmbeddingProvider:
    """Call Ollama's native embedding endpoint with one fixed model."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str = _DEFAULT_MODEL,
        dimension: int = _DEFAULT_DIMENSION,
        timeout: float | httpx.Timeout = 30.0,
        max_input_bytes: int = MAX_EMBEDDING_TEXT_BYTES,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        self._model = _validate_model(model)
        self._dimension = _validate_dimension(dimension)
        self._timeout = _validate_timeout(timeout)
        self._max_input_bytes = _validate_input_limit(max_input_bytes)
        self._max_response_bytes = _validate_response_limit(max_response_bytes)
        self._locked_digest: str | None = None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
        )

    @property
    def provider_name(self) -> str:
        return _PROVIDER

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def max_input_bytes(self) -> int:
        return self._max_input_bytes

    @property
    def model_digest(self) -> str | None:
        """Return the pinned digest after the alias has been resolved once."""

        return self._locked_digest

    async def __aenter__(self) -> OllamaEmbeddingProvider:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def resolve_model_digest(self) -> str:
        """Resolve the configured alias through Ollama and reject tag drift."""

        data = await self._request_json(method="GET", path="/api/tags")
        digest = _extract_model_digest(data, model_alias=self._model)
        if self._locked_digest is None:
            self._locked_digest = digest
        elif digest != self._locked_digest:
            raise EmbeddingModelDigestMismatchError(
                provider=_PROVIDER,
                expected=self._locked_digest,
                actual=digest,
            )
        return self._locked_digest

    async def embed(
        self,
        texts: Sequence[str],
        *,
        expected_dimension: int,
    ) -> EmbeddingBatch:
        if expected_dimension != self._dimension:
            raise EmbeddingConfigurationError(
                (
                    "Requested embedding dimension does not match the provider's fixed "
                    f"dimension {self._dimension}."
                ),
                provider=_PROVIDER,
            )
        if isinstance(texts, (str, bytes)) or not texts:
            raise EmbeddingInputError(
                "Ollama embedding input must be a non-empty sequence.",
                provider=_PROVIDER,
            )
        if any(not isinstance(text, str) or not text for text in texts):
            raise EmbeddingInputError(
                "Ollama embedding inputs must be non-empty strings.",
                provider=_PROVIDER,
            )
        for index, text in enumerate(texts):
            input_bytes = len(text.encode("utf-8"))
            if input_bytes > self._max_input_bytes:
                raise EmbeddingContextError(
                    (
                        f"Prepared Ollama embedding input {index} exceeds the reviewed "
                        f"{self._max_input_bytes}-byte context ceiling."
                    ),
                    provider=_PROVIDER,
                    maximum_input_bytes=self._max_input_bytes,
                    input_bytes=input_bytes,
                    input_index=index,
                )

        started = monotonic()
        model_digest = await self.resolve_model_digest()
        data = await self._request_json(
            method="POST",
            path="/api/embed",
            payload={
                "model": self._model,
                "input": list(texts),
                "truncate": False,
            },
        )
        # The configured model is a mutable Ollama alias. Re-resolve it after
        # inference so an alias retarget during the request cannot cause vectors
        # from a different artifact to be accepted under the pinned digest.
        model_digest = await self.resolve_model_digest()
        latency_ms = (monotonic() - started) * 1_000

        response_model = data.get("model")
        if not isinstance(response_model, str) or not response_model.strip():
            raise EmbeddingResponseError(
                "Ollama embedding response has no model identity.",
                provider=_PROVIDER,
            )
        if response_model != self._model:
            raise EmbeddingModelMismatchError(
                provider=_PROVIDER,
                expected=self._model,
                actual=response_model,
            )

        raw_embeddings = data.get("embeddings")
        if not isinstance(raw_embeddings, list):
            raise EmbeddingResponseError(
                "Ollama embedding response is missing list 'embeddings'.",
                provider=_PROVIDER,
            )
        if len(raw_embeddings) != len(texts):
            raise EmbeddingResponseError(
                (
                    "Ollama embedding response count does not match input count: "
                    f"received {len(raw_embeddings)}, expected {len(texts)}."
                ),
                provider=_PROVIDER,
            )

        embeddings: list[tuple[float, ...]] = []
        for index, raw_embedding in enumerate(raw_embeddings):
            if not isinstance(raw_embedding, list):
                raise EmbeddingResponseError(
                    f"Ollama embedding {index} is not a list.",
                    provider=_PROVIDER,
                )
            if len(raw_embedding) != self._dimension:
                raise EmbeddingDimensionError(
                    provider=_PROVIDER,
                    expected=self._dimension,
                    actual=len(raw_embedding),
                    index=index,
                )
            vector: list[float] = []
            for component in raw_embedding:
                if (
                    isinstance(component, bool)
                    or not isinstance(component, (int, float))
                    or not math.isfinite(component)
                ):
                    raise EmbeddingResponseError(
                        f"Ollama embedding {index} contains an invalid component.",
                        provider=_PROVIDER,
                    )
                vector.append(float(component))
            embeddings.append(tuple(vector))

        return EmbeddingBatch(
            embeddings=tuple(embeddings),
            model=response_model,
            model_digest=model_digest,
            dimension=self._dimension,
            latency_ms=latency_ms,
        )

    async def _request_json(
        self,
        *,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._client.stream(
                method,
                f"{self._base_url}{path}",
                json=payload,
                timeout=self._timeout,
            ) as response:
                _validate_content_length(
                    response.headers.get("content-length"),
                    maximum=self._max_response_bytes,
                )
                body = bytearray()
                chunk_size = min(_STREAM_CHUNK_BYTES, self._max_response_bytes + 1)
                async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                    if len(body) + len(chunk) > self._max_response_bytes:
                        raise EmbeddingResponseError(
                            "Ollama embedding response exceeds the configured size limit.",
                            provider=_PROVIDER,
                        )
                    body.extend(chunk)
                if not 200 <= response.status_code < 300:
                    if response.status_code in {400, 413} and _is_context_error(body):
                        raise EmbeddingContextError(
                            "Ollama rejected an embedding input that exceeds its context.",
                            provider=_PROVIDER,
                            maximum_input_bytes=self._max_input_bytes,
                        )
                    raise EmbeddingHTTPError(
                        f"Ollama embedding request failed with HTTP {response.status_code}.",
                        provider=_PROVIDER,
                        status_code=response.status_code,
                        retryable=response.status_code == 429 or response.status_code >= 500,
                    )
        except httpx.TimeoutException as exc:
            raise EmbeddingTimeoutError(
                "Ollama embedding request timed out.",
                provider=_PROVIDER,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise EmbeddingConnectionError(
                "Ollama embedding request could not reach the configured provider.",
                provider=_PROVIDER,
                retryable=True,
            ) from exc

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EmbeddingResponseError(
                "Ollama embedding response is not valid JSON.",
                provider=_PROVIDER,
            ) from exc
        if not isinstance(data, dict):
            raise EmbeddingResponseError(
                "Ollama embedding response is not a JSON object.",
                provider=_PROVIDER,
            )
        return data


def _validate_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise EmbeddingConfigurationError(
            "Ollama base URL must be a non-empty trimmed string.",
            provider=_PROVIDER,
        )
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise EmbeddingConfigurationError(
            "Ollama base URL must be an absolute HTTP(S) URL.",
            provider=_PROVIDER,
        )
    if parsed.username is not None or parsed.password is not None:
        raise EmbeddingConfigurationError(
            "Ollama base URL must not include credentials.",
            provider=_PROVIDER,
        )
    if parsed.query or parsed.fragment:
        raise EmbeddingConfigurationError(
            "Ollama base URL must not include a query or fragment.",
            provider=_PROVIDER,
        )
    if parsed.path not in {"", "/"}:
        raise EmbeddingConfigurationError(
            "Ollama base URL must not include a path.",
            provider=_PROVIDER,
        )
    return value.rstrip("/")


def _validate_model(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise EmbeddingConfigurationError(
            "Ollama embedding model must be a non-empty trimmed string.",
            provider=_PROVIDER,
        )
    return value


def _validate_dimension(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 4_096:
        raise EmbeddingConfigurationError(
            "Ollama embedding dimension must be between 1 and 4096.",
            provider=_PROVIDER,
        )
    return value


def _validate_timeout(value: float | httpx.Timeout) -> httpx.Timeout:
    if isinstance(value, httpx.Timeout):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise EmbeddingConfigurationError(
            "Ollama embedding timeout must be positive.",
            provider=_PROVIDER,
        )
    return httpx.Timeout(float(value))


def _validate_input_limit(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 256
        or value > MAX_EMBEDDING_TEXT_BYTES
    ):
        raise EmbeddingConfigurationError(
            (
                "Ollama embedding input limit must be between 256 and "
                f"{MAX_EMBEDDING_TEXT_BYTES} bytes."
            ),
            provider=_PROVIDER,
        )
    return value


def _validate_response_limit(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1_024
        or value > _MAX_RESPONSE_BYTES
    ):
        raise EmbeddingConfigurationError(
            "Ollama embedding response limit must be between 1024 and 64000000 bytes.",
            provider=_PROVIDER,
        )
    return value


def _validate_content_length(value: str | None, *, maximum: int) -> None:
    if value is None:
        return
    try:
        length = int(value, 10)
    except ValueError as exc:
        raise EmbeddingResponseError(
            "Ollama embedding response has an invalid Content-Length header.",
            provider=_PROVIDER,
        ) from exc
    if length < 0:
        raise EmbeddingResponseError(
            "Ollama embedding response has an invalid Content-Length header.",
            provider=_PROVIDER,
        )
    if length > maximum:
        raise EmbeddingResponseError(
            "Ollama embedding response exceeds the configured size limit.",
            provider=_PROVIDER,
        )


def _extract_model_digest(data: Mapping[str, Any], *, model_alias: str) -> str:
    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        raise EmbeddingResponseError(
            "Ollama tags response is missing its model list.",
            provider=_PROVIDER,
        )
    accepted_names = {model_alias}
    if ":" not in model_alias.rsplit("/", 1)[-1]:
        accepted_names.add(f"{model_alias}:latest")
    matches: set[str] = set()
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        names = {raw_model.get("name"), raw_model.get("model")}
        if not any(isinstance(name, str) and name in accepted_names for name in names):
            continue
        raw_digest = raw_model.get("digest")
        if not isinstance(raw_digest, str) or _SHA256_PATTERN.fullmatch(raw_digest) is None:
            raise EmbeddingResponseError(
                "Ollama tags response contains an invalid model digest.",
                provider=_PROVIDER,
            )
        try:
            matches.add(validate_model_digest(raw_digest))
        except ValueError as exc:  # pragma: no cover - guarded by the pattern
            raise EmbeddingResponseError(
                "Ollama tags response contains an invalid model digest.",
                provider=_PROVIDER,
            ) from exc
    if not matches:
        raise EmbeddingResponseError(
            "Configured Ollama embedding model is not installed.",
            provider=_PROVIDER,
        )
    if len(matches) != 1:
        raise EmbeddingResponseError(
            "Configured Ollama embedding alias resolves ambiguously.",
            provider=_PROVIDER,
        )
    return matches.pop()


def _is_context_error(body: bytes | bytearray) -> bool:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict) or not isinstance(data.get("error"), str):
        return False
    message = data["error"].casefold()
    return any(
        marker in message
        for marker in (
            "context length",
            "context window",
            "input length",
            "input is too large",
            "too many tokens",
        )
    )
