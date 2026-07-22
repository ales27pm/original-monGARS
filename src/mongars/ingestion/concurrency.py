"""Bounded process-local admission for document upload body parsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock


@dataclass(frozen=True, slots=True)
class UploadAdmissionSnapshot:
    """Non-sensitive counters exposed for deterministic tests and local metrics."""

    active_global: int
    active_for_owner: int


class DocumentUploadPermit:
    """One idempotently releasable upload admission."""

    __slots__ = ("_controller", "_token", "owner_id", "received_at")

    def __init__(
        self,
        *,
        controller: DocumentUploadAdmissionController,
        token: object,
        owner_id: str,
        received_at: datetime,
    ) -> None:
        self._controller = controller
        self._token = token
        self.owner_id = owner_id
        self.received_at = received_at

    def release(self) -> None:
        """Release this permit exactly once, including during request cancellation."""

        self._controller._release(self._token)


class DocumentUploadAdmissionController:
    """Reject excess uploads immediately instead of queueing request bodies in memory."""

    def __init__(self, *, global_limit: int, per_owner_limit: int) -> None:
        if isinstance(global_limit, bool) or not isinstance(global_limit, int) or global_limit < 1:
            raise ValueError("global document upload concurrency limit must be positive")
        if (
            isinstance(per_owner_limit, bool)
            or not isinstance(per_owner_limit, int)
            or per_owner_limit < 1
        ):
            raise ValueError("per-owner document upload concurrency limit must be positive")
        if per_owner_limit > global_limit:
            raise ValueError("per-owner upload concurrency cannot exceed the global limit")
        self._global_limit = global_limit
        self._per_owner_limit = per_owner_limit
        self._lock = Lock()
        self._active_by_token: dict[object, str] = {}
        self._active_by_owner: dict[str, int] = {}

    @property
    def global_limit(self) -> int:
        return self._global_limit

    @property
    def per_owner_limit(self) -> int:
        return self._per_owner_limit

    def try_acquire(self, *, owner_id: str) -> DocumentUploadPermit | None:
        """Acquire immediately or return ``None`` without waiting for capacity."""

        if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 255:
            raise ValueError("upload owner_id must be non-empty and at most 255 characters")
        with self._lock:
            owner_count = self._active_by_owner.get(owner_id, 0)
            if (
                len(self._active_by_token) >= self._global_limit
                or owner_count >= self._per_owner_limit
            ):
                return None
            token = object()
            self._active_by_token[token] = owner_id
            self._active_by_owner[owner_id] = owner_count + 1
        return DocumentUploadPermit(
            controller=self,
            token=token,
            owner_id=owner_id,
            received_at=datetime.now(UTC),
        )

    def snapshot(self, *, owner_id: str) -> UploadAdmissionSnapshot:
        with self._lock:
            return UploadAdmissionSnapshot(
                active_global=len(self._active_by_token),
                active_for_owner=self._active_by_owner.get(owner_id, 0),
            )

    def _release(self, token: object) -> None:
        with self._lock:
            owner_id = self._active_by_token.pop(token, None)
            if owner_id is None:
                return
            owner_count = self._active_by_owner[owner_id]
            if owner_count == 1:
                del self._active_by_owner[owner_id]
            else:
                self._active_by_owner[owner_id] = owner_count - 1


__all__ = [
    "DocumentUploadAdmissionController",
    "DocumentUploadPermit",
    "UploadAdmissionSnapshot",
]
