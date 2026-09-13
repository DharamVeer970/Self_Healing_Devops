"""LangGraph engine: routing decision functions (pure)."""
import pytest

from agent import graph as g



pytestmark = pytest.mark.skipif(not g.HAS_LANGGRAPH,
                                reason="langgraph not installed")


# ---- route_after_monitor ---------------------------------------------------

def test_monitor_errors_to_diagnose():
    assert g.route_after_monitor({"errors": ["ERROR x"]}) == "diagnose"


def test_monitor_no_errors_no_attempt_clear():
    assert g.route_after_monitor({"errors": []}) == "clear"


def test_monitor_no_errors_with_attempt_report():
    assert g.route_after_monitor({"errors": [], "attempt": 2}) == "report"


def test_monitor_missing_errors_key_clear():
    assert g.route_after_monitor({}) == "clear"


# ---- route_after_diagnose --------------------------------------------------

def test_diagnose_known_to_safety():
    assert g.route_after_diagnose(
        {"diagnosis": {"action": "rotate_logs"},
         "fresh_diagnosis": True}) == "safety"


def test_diagnose_none_no_attempt_clear():
    assert g.route_after_diagnose({"diagnosis": None}) == "clear"


def test_diagnose_none_with_attempt_report():
    assert g.route_after_diagnose({"diagnosis": None, "attempt": 1}) \
        == "report"


# ---- route_after_safety ----------------------------------------------------

def test_safety_allowed_known_to_remediate():
    assert g.route_after_safety(
        {"allowed": True, "diagnosis": {"confidence": 0.93}}) == "remediate"


def test_safety_blocked_to_report():
    assert g.route_after_safety(
        {"allowed": False, "diagnosis": {"confidence": 0.93}}) == "report"


def test_safety_unknown_confidence_to_report():
    assert g.route_after_safety(
        {"allowed": True, "diagnosis": {"confidence": 0.0}}) == "report"


# ---- route_after_remediate -------------------------------------------------

def test_remediate_declined_to_report():
    assert g.route_after_remediate({"declined": True}) == "report"


def test_remediate_not_declined_to_verify():
    assert g.route_after_remediate({"declined": False}) == "verify"


# ---- route_after_verify ----------------------------------------------------

def test_verify_healthy_to_report():
    assert g.route_after_verify({"verification": (True, [], {})}) == "report"


def test_verify_attempts_exhausted_to_report():
    assert g.route_after_verify(
        {"verification": (False, ["x"], {}),
         "attempt": g.MAX_ATTEMPTS}) == "report"


def test_verify_still_broken_retries():
    assert g.route_after_verify(
        {"verification": (False, ["x"], {}), "attempt": 1}) == "monitor"


def test_verify_missing_verification_is_broken():
    # Default is (False, [...]) => not ok => retry.
    assert g.route_after_verify({"attempt": 1}) == "monitor"
