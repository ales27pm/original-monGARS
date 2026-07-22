import pytest

from mongars.security.policy import (
    ActionClassification,
    PolicyDecision,
    ToolPolicy,
)


@pytest.fixture
def policy() -> ToolPolicy:
    return ToolPolicy(
        {
            ("memory", "search"): ActionClassification.READ_ONLY,
            ("memory", "create_note"): ActionClassification.LOCAL_MUTATION,
            ("mail", "send"): ActionClassification.EXTERNAL_SIDE_EFFECT,
        }
    )


def test_registered_read_only_action_is_allowed(policy: ToolPolicy) -> None:
    result = policy.evaluate("memory", "search")

    assert result.decision is PolicyDecision.ALLOW
    assert result.classification is ActionClassification.READ_ONLY
    assert result.allowed is True
    assert result.requires_approval is False


@pytest.mark.parametrize(
    ("tool", "action", "classification"),
    [
        ("memory", "create_note", ActionClassification.LOCAL_MUTATION),
        ("mail", "send", ActionClassification.EXTERNAL_SIDE_EFFECT),
    ],
)
def test_mutating_actions_require_approval(
    policy: ToolPolicy,
    tool: str,
    action: str,
    classification: ActionClassification,
) -> None:
    result = policy.evaluate(tool, action)

    assert result.decision is PolicyDecision.REQUIRES_APPROVAL
    assert result.classification is classification
    assert result.allowed is False
    assert result.requires_approval is True


@pytest.mark.parametrize(
    ("tool", "action"),
    [
        ("unknown", "search"),
        ("memory", "unknown"),
        ("Memory", "search"),
        ("memory", " search"),
        ("", "search"),
        (None, "search"),
        ([], "search"),
    ],
)
def test_unknown_or_non_exact_actions_are_denied(
    policy: ToolPolicy,
    tool: object,
    action: object,
) -> None:
    result = policy.evaluate(tool, action)

    assert result.decision is PolicyDecision.DENY
    assert result.classification is None
    assert result.allowed is False
    assert result.requires_approval is False


def test_rules_are_copied_at_construction() -> None:
    rules = {("memory", "search"): ActionClassification.READ_ONLY}
    policy = ToolPolicy(rules)
    rules[("mail", "send")] = ActionClassification.EXTERNAL_SIDE_EFFECT

    assert policy.evaluate("mail", "send").decision is PolicyDecision.DENY


@pytest.mark.parametrize(
    "bad_key",
    [
        ("memory", ""),
        ("memory",),
        "memory.search",
    ],
)
def test_invalid_rule_keys_are_rejected(bad_key: object) -> None:
    with pytest.raises(ValueError, match="policy rule keys"):
        ToolPolicy({bad_key: ActionClassification.READ_ONLY})  # type: ignore[dict-item]
