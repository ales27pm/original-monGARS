"""Shared fail-closed action registry for the API and production worker runtime."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from mongars.security.policy import ActionClassification, RuleKey, ToolPolicy

CONTROL_PLANE_ACTIONS: Mapping[RuleKey, ActionClassification] = MappingProxyType(
    {
        ("memory", "search"): ActionClassification.READ_ONLY,
        ("memory", "note.create"): ActionClassification.LOCAL_MUTATION,
        ("memory", "reindex"): ActionClassification.LOCAL_MUTATION,
        ("document", "ingest"): ActionClassification.LOCAL_MUTATION,
        ("personality", "profile.apply"): ActionClassification.LOCAL_MUTATION,
        ("personality", "profile.reset"): ActionClassification.LOCAL_MUTATION,
        ("personality", "profile.delete"): ActionClassification.LOCAL_MUTATION,
    }
)


def build_control_plane_policy() -> ToolPolicy:
    """Return one immutable policy instance from the shared action registry."""

    return ToolPolicy(CONTROL_PLANE_ACTIONS)


__all__ = ["CONTROL_PLANE_ACTIONS", "build_control_plane_policy"]
