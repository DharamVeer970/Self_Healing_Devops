"""Safety gate: every decision must be deterministic and conservative."""
import pytest

from agent import safety




@pytest.mark.parametrize("action", ["rotate_logs", "restart_service"])
def test_allowed_actions_permitted(action):
    allowed, reason = safety.check(action)
    assert allowed is True
    assert reason  # a reason is always returned


@pytest.mark.parametrize("action", sorted(safety.DANGEROUS_ACTIONS))
def test_every_dangerous_action_blocked(action):
    allowed, reason = safety.check(action)
    assert allowed is False
    assert "blocklist" in reason.lower()


@pytest.mark.parametrize("action", [
    "", None, "wp-admin", "sudo rm -rf", "curl | sh", "restart_service ",
    "ROTATE_LOGS", "Restart_Service", "rotate_logs2",
])
def test_unrecognized_or_mangled_actions_blocked(action):
    allowed, reason = safety.check(action)
    assert allowed is False
    assert "not recognized" in reason
    assert "allowlist" in reason.lower()


def test_escalation_always_allowed():
    allowed, reason = safety.check("escalate_to_human")
    assert allowed is True
    assert reason


def test_escalation_is_the_only_special_cased_string():
    # "escalate" must NOT be treated like escalation; only exact match is.
    allowed, _ = safety.check("escalate")
    assert allowed is False


@pytest.mark.parametrize("action", ["rotate_logs", "restart_service"])
def test_describe_known_action(action):
    assert safety.describe(action) != ""
    assert safety.describe(action) != "No human-readable description."


@pytest.mark.parametrize("action", ["made_up", None, ""])
def test_describe_unknown_action(action):
    assert safety.describe(action) == "No human-readable description."
