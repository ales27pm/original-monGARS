"""Public orchestrator API surface."""

from __future__ import annotations

from importlib import import_module


def __getattr__(name: str):
    if name not in _ORCHESTRATOR_EXPORTS:
        raise AttributeError(f"module 'mongars.orchestrator' has no attribute {name!r}")
    module_name, attr = _ORCHESTRATOR_EXPORTS[name]
    module = import_module(f"mongars.orchestrator.{module_name}")
    value = getattr(module, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__ + [*globals().keys()]))


_ORCHESTRATOR_EXPORTS = {
    "MAX_COGNITIVE_CONTEXT_BYTES": ("cognitive_context", "MAX_COGNITIVE_CONTEXT_BYTES"),
    "PERSONALITY_DIMENSIONS": ("personality", "PERSONALITY_DIMENSIONS"),
    "AffectLabel": ("emotion", "AffectLabel"),
    "AffectSignal": ("emotion", "AffectSignal"),
    "AffectSource": ("emotion", "AffectSource"),
    "ChatResult": ("cortex", "ChatResult"),
    "Cortex": ("cortex", "Cortex"),
    "PersonalityDimension": ("personality", "PersonalityDimension"),
    "PersonalityPreference": ("personality", "PersonalityPreference"),
    "PersonalitySnapshot": ("personality", "PersonalitySnapshot"),
    "PersonalitySource": ("personality", "PersonalitySource"),
    "TypedChatResult": ("typed_chat", "TypedChatResult"),
    "TypedChatRuntime": ("typed_chat", "TypedChatRuntime"),
    "serialize_cognitive_context": ("cognitive_context", "serialize_cognitive_context"),
}


__all__ = [
    "MAX_COGNITIVE_CONTEXT_BYTES",
    "PERSONALITY_DIMENSIONS",
    "AffectLabel",
    "AffectSignal",
    "AffectSource",
    "ChatResult",
    "Cortex",
    "PersonalityDimension",
    "PersonalityPreference",
    "PersonalitySnapshot",
    "PersonalitySource",
    "TypedChatResult",
    "TypedChatRuntime",
    "serialize_cognitive_context",
]
