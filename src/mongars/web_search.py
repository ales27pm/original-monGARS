"""Bounded SearxNG web-search adapter with stable failure semantics."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit

import httpx

type WebSearchErrorCode = Literal[
    "disabled",
    "invalid_request",
    "connection_error",
    "timeout",
    "http_error",
    "malformed_response",
    "response_too_large",
    "no_results",
]
type WebSearchHealthErrorCode = (
    WebSearchErrorCode
    | Literal[
        "not_configured",
        "unexpected_error",
    ]
)

_PLAINTEXT_HOSTS = frozenset({"localhost", "127.0.0.1", "searxng"})
_MAX_QUERY_CHARS_HARD_LIMIT = 2_000
_MAX_RESULTS_HARD_LIMIT = 50
_MAX_RESPONSE_BYTES_HARD_LIMIT = 10_000_000
_MAX_RESULT_URL_CHARS = 4_096
_MAX_TITLE_CHARS = 300
_MAX_SNIPPET_CHARS = 2_000
_STREAM_CHUNK_BYTES = 64 * 1024

_COMMAND_BOUNDARY = r"(?:^|(?<=[.!?])\s+)"
_ENGLISH_POLITE = r"(?:(?:could|can|would|will)\s+you\s+)?(?:please\s+)?"
_FRENCH_POLITE = (
    r"(?:(?:peux-tu|pouvez-vous|pourrais-tu|pourriez-vous|"
    r"est-ce\s+que\s+tu\s+peux|est-ce\s+que\s+vous\s+pouvez)\s+)?"
    r"(?:s['’]il\s+(?:te|vous)\s+pla[iî]t\s+)?"  # noqa: RUF001
)
_SEARCH_COMMAND_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        _COMMAND_BOUNDARY
        + _ENGLISH_POLITE
        + r"(?:(?:search|browse|check)\s+(?:the\s+)?(?:web|internet|online)|"
        r"(?:do|run)\s+(?:a\s+)?web\s+search)\b\s*"
        r"(?:(?:for|about|on)\s+)?(?:and\s+)?(?:[:,\-]\s*)?",
        _COMMAND_BOUNDARY
        + _ENGLISH_POLITE
        + r"(?:look|check)\s+(?P<query>this|that|it|them)\s+up\s+online\b",
        _COMMAND_BOUNDARY
        + _ENGLISH_POLITE
        + r"(?:look\s+up|check)\s+(?P<query>[^.?!\n]{1,200}?)\s+online\b",
        _COMMAND_BOUNDARY + _ENGLISH_POLITE + r"find\s+(?P<query>[^.?!\n]{1,200}?)\s+"
        r"(?:on\s+the\s+(?:web|internet)|online)\b",
        _COMMAND_BOUNDARY + _ENGLISH_POLITE + r"google\s+(?P<query>[^.?!\n]{1,200})(?=$|[.?!])",
        _COMMAND_BOUNDARY
        + _FRENCH_POLITE
        + r"(?:cherche|cherchez|chercher|recherche|recherchez|rechercher|"
        r"v[ée]rifie|v[ée]rifiez|v[ée]rifier)\s+"
        r"(?P<query>[^.?!\n]{1,200}?)\s+"
        r"(?:sur\s+(?:le\s+)?(?:web|internet)|en\s+ligne)\b",
        _COMMAND_BOUNDARY
        + _FRENCH_POLITE
        + r"(?:cherche|cherchez|chercher|recherche|recherchez|rechercher|"
        r"v[ée]rifie|v[ée]rifiez|v[ée]rifier)\s+"
        r"(?:sur\s+(?:le\s+)?(?:web|internet)|en\s+ligne)\b\s*"
        r"(?:(?:pour|sur|au\s+sujet\s+de)\s+)?(?:[:,\-]\s*)?",
        _COMMAND_BOUNDARY + _FRENCH_POLITE + r"(?:fais|faites|faire)\s+(?:une\s+)?recherche\s+"
        r"(?:web|internet|en\s+ligne)\b\s*"
        r"(?:(?:pour|sur|au\s+sujet\s+de)\s+)?(?:[:,\-]\s*)?",
    )
)
_TRAILING_POLITENESS = re.compile(
    r"^\s*,?\s*(?:please|s['’]il\s+(?:te|vous)\s+pla[iî]t)\s*[.!?]*\s*$",  # noqa: RUF001
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """One validated and bounded search result."""

    title: str
    url: str
    snippet: str
    engine: str | None = None


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """A normalized SearxNG response captured at a UTC instant."""

    query: str
    results: tuple[WebSearchResult, ...]
    retrieved_at: datetime

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is not UTC:
            raise ValueError("retrieved_at must use the UTC timezone")


@dataclass(frozen=True, slots=True)
class WebSearchHealthStatus:
    """A non-throwing SearxNG status suitable for readiness checks."""

    enabled: bool
    healthy: bool
    latency_ms: float
    error_code: WebSearchHealthErrorCode | None = None


class WebSearchError(RuntimeError):
    """Stable error returned by the bounded web-search adapter."""

    def __init__(
        self,
        message: str,
        *,
        code: WebSearchErrorCode,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class SearxNGSearchBackend:
    """Query one fixed SearxNG origin and normalize its JSON results."""

    def __init__(
        self,
        *,
        base_url: str | None,
        enabled: bool = True,
        timeout: float | httpx.Timeout = 10.0,
        max_query_chars: int = 500,
        max_results: int = 8,
        max_response_bytes: int = 1_000_000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if enabled and base_url is None:
            raise ValueError("base_url is required when web search is enabled")
        self._base_url = _validate_origin(base_url) if base_url is not None else None
        self._enabled = enabled
        self._timeout = _validate_timeout(timeout)
        self._max_query_chars = _bounded_positive_int(
            max_query_chars,
            field="max_query_chars",
            hard_limit=_MAX_QUERY_CHARS_HARD_LIMIT,
        )
        self._max_results = _bounded_positive_int(
            max_results,
            field="max_results",
            hard_limit=_MAX_RESULTS_HARD_LIMIT,
        )
        self._max_response_bytes = _bounded_positive_int(
            max_response_bytes,
            field="max_response_bytes",
            hard_limit=_MAX_RESPONSE_BYTES_HARD_LIMIT,
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(follow_redirects=False, trust_env=False)

    async def __aenter__(self) -> SearxNGSearchBackend:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the HTTP client only when the backend created it."""

        if self._owns_client:
            await self._client.aclose()

    async def search(self, query: str, *, limit: int | None = None) -> SearchResponse:
        """Perform a bounded, non-redirecting SearxNG JSON search."""

        if not self._enabled or self._base_url is None:
            raise WebSearchError(
                "Web search is disabled.",
                code="disabled",
                retryable=False,
            )

        normalized_query = self._validate_query(query)
        result_limit = self._validate_result_limit(limit)
        endpoint = f"{self._base_url}/search"

        try:
            async with self._client.stream(
                "GET",
                endpoint,
                params={"q": normalized_query, "format": "json"},
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code != httpx.codes.OK:
                    raise WebSearchError(
                        f"SearxNG returned HTTP {response.status_code}.",
                        code="http_error",
                        retryable=_retryable_status(response.status_code),
                        status_code=response.status_code,
                    )
                body = await self._read_bounded_body(response)
        except WebSearchError:
            raise
        except httpx.TimeoutException as exc:
            raise WebSearchError(
                "SearxNG search timed out.",
                code="timeout",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise WebSearchError(
                "Could not connect to SearxNG.",
                code="connection_error",
                retryable=True,
            ) from exc

        data = _decode_json_object(body)
        results = _normalize_results(data, limit=result_limit)
        if not results:
            raise WebSearchError(
                "SearxNG returned no usable results.",
                code="no_results",
                retryable=False,
            )
        return SearchResponse(
            query=normalized_query,
            # SearXNG has already ranked the merged engine results. Preserve that ordering:
            # a domain-name heuristic can otherwise promote stale preview pages above a later
            # result that contains the outcome the user actually asked for.
            results=results,
            retrieved_at=datetime.now(UTC),
        )

    async def health(self) -> WebSearchHealthStatus:
        """Probe SearxNG configuration without issuing a search query."""

        started = monotonic()
        if not self._enabled or self._base_url is None:
            return WebSearchHealthStatus(
                enabled=False,
                healthy=True,
                latency_ms=(monotonic() - started) * 1000,
            )

        try:
            async with self._client.stream(
                "GET",
                f"{self._base_url}/config",
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code != httpx.codes.OK:
                    raise WebSearchError(
                        f"SearXNG returned HTTP {response.status_code}.",
                        code="http_error",
                        retryable=_retryable_status(response.status_code),
                        status_code=response.status_code,
                    )
                body = await self._read_bounded_body(response)
            _decode_json_object(body)
        except WebSearchError as exc:
            return WebSearchHealthStatus(
                enabled=True,
                healthy=False,
                latency_ms=(monotonic() - started) * 1000,
                error_code=exc.code,
            )
        except httpx.TimeoutException:
            return WebSearchHealthStatus(
                enabled=True,
                healthy=False,
                latency_ms=(monotonic() - started) * 1000,
                error_code="timeout",
            )
        except httpx.RequestError:
            return WebSearchHealthStatus(
                enabled=True,
                healthy=False,
                latency_ms=(monotonic() - started) * 1000,
                error_code="connection_error",
            )
        except Exception:
            return WebSearchHealthStatus(
                enabled=True,
                healthy=False,
                latency_ms=(monotonic() - started) * 1000,
                error_code="unexpected_error",
            )

        return WebSearchHealthStatus(
            enabled=True,
            healthy=True,
            latency_ms=(monotonic() - started) * 1000,
        )

    def _validate_query(self, query: str) -> str:
        if not isinstance(query, str):
            raise _invalid_request("Web-search query must be a string.")
        normalized = query.strip()
        if not normalized:
            raise _invalid_request("Web-search query must not be empty.")
        if len(normalized) > self._max_query_chars:
            raise _invalid_request(f"Web-search query exceeds {self._max_query_chars} characters.")
        return normalized

    def _validate_result_limit(self, limit: int | None) -> int:
        if limit is None:
            return self._max_results
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= self._max_results
        ):
            raise _invalid_request(f"Result limit must be between 1 and {self._max_results}.")
        return limit

    async def _read_bounded_body(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = -1
            if declared_length > self._max_response_bytes:
                raise _response_too_large(self._max_response_bytes)

        body = bytearray()
        chunk_size = min(_STREAM_CHUNK_BYTES, self._max_response_bytes + 1)
        async for chunk in response.aiter_bytes(chunk_size=chunk_size):
            if len(body) + len(chunk) > self._max_response_bytes:
                raise _response_too_large(self._max_response_bytes)
            body.extend(chunk)
        return bytes(body)


def explicit_web_search_requested(text: str) -> bool:
    """Return whether text explicitly requests public-web lookup.

    Generic local searches, negations, quotations, and incidental prose do not match.
    Imperative commands may begin the request or a new sentence. This helper deliberately
    favors false negatives over surprising egress.
    """

    if not isinstance(text, str) or not text.strip():
        return False
    return _find_search_command(text) is not None


def search_query_from_request(text: str, *, max_chars: int) -> str:
    """Remove the detected search command while retaining the user's factual query."""

    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError("max_chars must be a positive integer")
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    normalized = " ".join(text.split())
    match = _find_search_command(normalized)
    if match is None:
        return normalized[:max_chars].rstrip()

    before = normalized[: match.start()].strip()
    after = _TRAILING_POLITENESS.sub("", normalized[match.end() :]).strip()
    embedded = match.groupdict().get("query", "").strip()
    if _has_query_text(before):
        candidate = " ".join(part for part in (before, after) if _has_query_text(part))
    else:
        candidate = " ".join(part for part in (embedded, after) if _has_query_text(part))
    if not candidate:
        candidate = normalized
    return candidate[:max_chars].rstrip()


def _find_search_command(text: str) -> re.Match[str] | None:
    for pattern in _SEARCH_COMMAND_PATTERNS:
        match = pattern.search(text)
        if match is not None and not _match_is_quoted(text, match):
            return match
    return None


def _match_is_quoted(text: str, match: re.Match[str]) -> bool:
    command_start = match.start()
    for opening, closing in (('"', '"'), ("`", "`"), ("“", "”"), ("«", "»")):
        before = text[:command_start]
        if opening == closing:
            if before.count(opening) % 2:
                return True
            continue
        if before.rfind(opening) > before.rfind(closing) and text.find(closing, match.end()) != -1:
            return True
    return False


def _has_query_text(value: str) -> bool:
    return bool(value.strip(" \t\r\n.,!?;:-"))


def _validate_origin(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("base_url must be a non-empty HTTP(S) origin")
    if "?" in value or "#" in value:
        raise ValueError("base_url must not contain a query or fragment")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base_url is not a valid HTTP(S) origin") from exc

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or hostname is None:
        raise ValueError("base_url must be an absolute HTTP(S) origin")
    hostname = hostname.lower()
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain credentials")
    if parsed.path not in {"", "/"}:
        raise ValueError("base_url must not contain a path")
    if scheme == "http" and hostname not in _PLAINTEXT_HOSTS:
        raise ValueError("plaintext SearxNG is allowed only on localhost, 127.0.0.1, or searxng")
    if _contains_unsafe_url_character(value):
        raise ValueError("base_url contains whitespace or control characters")

    host = _format_hostname(hostname)
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def _decode_json_object(body: bytes) -> Mapping[str, Any]:
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebSearchError(
            "SearxNG returned malformed JSON.",
            code="malformed_response",
            retryable=False,
        ) from exc
    if not isinstance(data, Mapping):
        raise WebSearchError(
            "SearxNG response must be a JSON object.",
            code="malformed_response",
            retryable=False,
        )
    return cast(Mapping[str, Any], data)


def _normalize_results(
    data: Mapping[str, Any],
    *,
    limit: int,
) -> tuple[WebSearchResult, ...]:
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raise WebSearchError(
            "SearxNG response is missing a results list.",
            code="malformed_response",
            retryable=False,
        )

    normalized: list[WebSearchResult] = []
    seen_urls: set[str] = set()
    for raw_result in raw_results:
        if len(normalized) >= limit:
            break
        if not isinstance(raw_result, Mapping):
            continue
        url = _normalize_result_url(raw_result.get("url"))
        if url is None or url in seen_urls:
            continue
        seen_urls.add(url)

        title = _bounded_text(raw_result.get("title"), limit=_MAX_TITLE_CHARS)
        snippet = _bounded_text(raw_result.get("content"), limit=_MAX_SNIPPET_CHARS)
        engine = _optional_bounded_text(raw_result.get("engine"), limit=100)
        normalized.append(
            WebSearchResult(
                title=title or url,
                url=url,
                snippet=snippet,
                engine=engine,
            )
        )
    return tuple(normalized)


def _normalize_result_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or len(value) > _MAX_RESULT_URL_CHARS:
        return None
    if value != value.strip() or _contains_unsafe_url_character(value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if scheme not in {"http", "https"} or hostname is None:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None

    hostname = hostname.lower()
    host = _format_hostname(hostname)
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, host, path, parsed.query, ""))


def _bounded_text(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    safe = value.encode("utf-8", errors="replace").decode("utf-8")
    normalized = " ".join(safe.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def _optional_bounded_text(value: object, *, limit: int) -> str | None:
    bounded = _bounded_text(value, limit=limit)
    return bounded or None


def _format_hostname(hostname: str) -> str:
    return f"[{hostname}]" if ":" in hostname else hostname


def _contains_unsafe_url_character(value: str) -> bool:
    return any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    )


def _validate_timeout(value: float | httpx.Timeout) -> httpx.Timeout:
    if isinstance(value, httpx.Timeout):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("timeout must be a positive number or httpx.Timeout")
    return httpx.Timeout(float(value))


def _bounded_positive_int(value: int, *, field: str, hard_limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= hard_limit:
        raise ValueError(f"{field} must be between 1 and {hard_limit}")
    return value


def _invalid_request(message: str) -> WebSearchError:
    return WebSearchError(message, code="invalid_request", retryable=False)


def _response_too_large(max_bytes: int) -> WebSearchError:
    return WebSearchError(
        f"SearxNG response exceeds {max_bytes} bytes.",
        code="response_too_large",
        retryable=False,
    )


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429} or status_code >= 500


__all__ = [
    "SearchResponse",
    "SearxNGSearchBackend",
    "WebSearchError",
    "WebSearchHealthStatus",
    "WebSearchResult",
    "explicit_web_search_requested",
    "search_query_from_request",
]
