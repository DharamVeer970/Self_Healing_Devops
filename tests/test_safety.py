"""SAFETY NODE - the allowlist/blocklist gate is what makes the agent safe."""
from agent import safety


def test_allowlisted_action_is_permitted():
    for action in ("rotate_logs", "restart_service"):
        allowed, reason = safety.check(action)
        assert allowed is True
        assert reason


def test_dangerous_action_is_permanently_blocked():
    for action in safety.DANGEROUS_ACTIONS:
        allowed, reason = safety.check(action)
        assert allowed is False
        assert "blocklist" in reason


def test_unknown_action_is_blocked():
    allowed, reason = safety.check("reinstall_os_by_guessing")
    assert allowed is False
    assert "not recognized" in reason


def test_escalation_always_allowed():
    allowed, reason = safety.check("escalate_to_human")
    assert allowed is True


def test_describe_known_and_unknown_actions():
    assert safety.describe("rotate_logs") != ""
    assert safety.describe("made_up") == "No human-readable description."
