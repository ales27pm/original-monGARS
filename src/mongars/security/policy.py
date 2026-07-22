"""Fail-closed policy decisions for model-proposed tool actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class ActionClassification(StrEnum):
    """Security impact of a registered tool action."""

    READ_ONLY = "read_only"
    LOCAL_MUTATION = "local_mutation"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class PolicyDecision(StrEnum):
    """A deterministic decision made independently from the model."""

    ALLOW = "allow"
    REQUIRES_APPROVAL = "requires_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """Policy decision with an auditable classification and reason."""

    decision: PolicyDecision
    classification: ActionClassification | None
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision is PolicyDecision.ALLOW

    @property
    def requires_approval(self) -> bool:
        return self.decision is PolicyDecision.REQUIRES_APPROVAL


RuleKey = tuple[str, str]


class ToolPolicy:
    """Classify registered ``(tool, action)`` pairs and deny everything else.

    Policy rules are copied into an immutable mapping at construction so a
    caller cannot mutate authorization behavior after the policy is installed.
    Exact, case-sensitive matching prevents normalization differences between
    routing and authorization layers.
    """

    def __init__(self, rules: Mapping[RuleKey, ActionClassification | str]) -> None:
        normalized: dict[RuleKey, ActionClassification] = {}
        for key, classification in rules.items():
            if (
                not isinstance(key, tuple)
                or len(key) != 2
                or not all(isinstance(part, str) and part for part in key)
            ):
                raise ValueError("policy rule keys must be non-empty (tool, action) strings")
            normalized[key] = ActionClassification(classification)
        self._rules: Mapping[RuleKey, ActionClassification] = MappingProxyType(normalized)

    def classify(self, tool: object, action: object) -> ActionClassification | None:
        """Return the registered classification, or ``None`` when unknown."""

        if not isinstance(tool, str) or not isinstance(action, str):
            return None
        if not tool or not action:
            return None
        return self._rules.get((tool, action))

    def evaluate(self, tool: object, action: object) -> PolicyResult:
        """Return a fail-closed decision for a proposed tool action."""

        classification = self.classify(tool, action)
        if classification is None:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                classification=None,
                reason="unknown tool or action",
            )

        if classification is ActionClassification.READ_ONLY:
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                classification=classification,
                reason="registered read-only action",
            )

        return PolicyResult(
            decision=PolicyDecision.REQUIRES_APPROVAL,
            classification=classification,
            reason=f"{classification.value} action requires approval",
        )
