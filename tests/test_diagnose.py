"""Diagnose node: rule classification edge cases."""
import pytest

from agent import diagnose



DISK = "ERROR OSError: [Errno 28] No space left on device (disk=97%)"
OOM = "FATAL Process crashed - MemoryError: OutOfMemoryError 256MiB"
BACKEND = "ERROR upstream server unavailable: 502 Bad Gateway"
CONNREF = "ERROR connect() failed (111: Connection refused)"
UNKNOWN = "ERROR Segmentation fault (core dumped)"


# ---- classify --------------------------------------------------------------

def test_classify_empty_returns_none():
    assert diagnose.classify([]) is None


def test_classify_none_returns_none():
    assert diagnose.classify(None) is None


@pytest.mark.parametrize("line", [
    DISK,
    "no space left on device",                     # lowercase via IGNORECASE
    "No SPACE Left On Device",                     # mixed case
    "alert: NO SPACE LEFT ON DEVICE",              # embedded
])
def test_disk_full_detected(line):
    r = diagnose.classify([line])
    assert r["action"] == "rotate_logs"
    assert r["confidence"] == 0.97


@pytest.mark.parametrize("line", [
    OOM,
    "OutOfMemoryError: unable to allocate",
    "MemoryError at line 12",
    "java.lang.OutOfMemoryError",
])
def test_oom_detected(line):
    r = diagnose.classify([line])
    assert r["action"] == "restart_service"
    assert r["confidence"] == 0.93


@pytest.mark.parametrize("line", [
    BACKEND,
    CONNREF,
    "502 Bad Gateway from upstream",
    "upstream server temporarily unavailable",
    "upstream timed out: unavailable",
])
def test_backend_detected(line):
    r = diagnose.classify([line])
    assert r["action"] == "restart_service"
    assert r["confidence"] == 0.90


@pytest.mark.parametrize("line", [
    UNKNOWN,
    "ERROR something weird happened",
    "Kernel panic",
    "Connection reset by peer",
    "No space",                                     # not the full phrase
    "OutOfMemory",                                  # not a full match
])
def test_unknown_escalates(line):
    r = diagnose.classify([line])
    assert r["action"] == "escalate_to_human"
    assert r["confidence"] == 0.0
    assert r["error_line"] == line.strip()


def test_classify_strips_error_line():
    r = diagnose.classify(["   " + DISK + "   "])
    assert r["error_line"] == DISK


def test_newest_error_wins():
    r = diagnose.classify([BACKEND, OOM, DISK])
    # newest (last) is DISK -> rotate_logs wins.
    assert r["action"] == "rotate_logs"
    assert r["error_line"] == DISK


def test_inner_error_wins_over_final_unknown():
    # reversed() scans newest->oldest; last list element (UNKNOWN) matches
    # no rule, so we must continue to the earlier DISK line.
    r = diagnose.classify([UNKNOWN, DISK])
    assert r["action"] == "rotate_logs"


def test_classify_returns_new_dict_not_mutating_rules():
    r1 = diagnose.classify([DISK])
    r2 = diagnose.classify([DISK])
    assert r1 is not r2
    assert "error_line" not in diagnose.RULES[0]   # original untouched


def test_classify_result_carries_diagnosis_text():
    r = diagnose.classify([DISK])
    assert r["diagnosis"] == diagnose.RULES[0]["diagnosis"]
