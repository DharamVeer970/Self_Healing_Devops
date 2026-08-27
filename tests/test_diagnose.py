"""DIAGNOSE NODE - rule classification must be deterministic and instant."""
from agent import diagnose

DISK_ERROR = ("2026-08-27 10:00:01 | ERROR [pid 123] OSError: [Errno 28] "
              "No space left on device - failed to write (disk=97%)")
OOM_CRASH = ("2026-08-27 10:00:02 | FATAL [pid 123] Process crashed - "
             "MemoryError: OutOfMemoryError unable to allocate 256 MiB")
BACKEND_DOWN = ("2026-08-27 10:00:03 | ERROR [pid 7] upstream server "
                "temporarily unavailable: 502 Bad Gateway Connection refused")
UNKNOWN = "2026-08-27 10:00:04 | ERROR [pid 9] Segmentation fault (core dumped)"


def test_empty_input_has_no_diagnosis():
    assert diagnose.classify([]) is None


def test_disk_full_maps_to_rotate_logs():
    result = diagnose.classify([DISK_ERROR])
    assert result["action"] == "rotate_logs"
    assert result["confidence"] == 0.97
    assert result["error_line"] == DISK_ERROR


def test_oom_crash_maps_to_restart_service():
    result = diagnose.classify([OOM_CRASH])
    assert result["action"] == "restart_service"
    assert result["confidence"] == 0.93


def test_backend_down_maps_to_restart_service():
    for line in (BACKEND_DOWN,
                 "ERROR backend connect() failed (111: Connection refused)"):
        result = diagnose.classify([line])
        assert result["action"] == "restart_service"


def test_unknown_error_escalates():
    result = diagnose.classify([UNKNOWN])
    assert result["action"] == "escalate_to_human"
    assert result["confidence"] == 0.0


def test_newest_error_line_wins():
    result = diagnose.classify([BACKEND_DOWN, OOM_CRASH, DISK_ERROR])
    # reversed() order: newest line checked first -> disk rule matches.
    assert result["action"] == "rotate_logs"
    assert result["error_line"] == DISK_ERROR


def test_info_lines_do_not_match_any_rule():
    assert diagnose.classify(
        ["INFO GET /health 200 OK"]) ["action"] == "escalate_to_human"
