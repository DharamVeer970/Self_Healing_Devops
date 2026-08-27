"""MONITOR NODE - log tailing, error extraction and state reading."""
import json

from agent import monitor


def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_extract_errors_only_keeps_severe_lines():
    lines = [
        "INFO  heartbeat ok",
        "ERROR disk full",
        "FATAL crashed",
        "WARN noisy but fine",
    ]
    assert monitor.extract_errors(lines) == [
        "ERROR disk full", "FATAL crashed",
    ]


def test_read_new_lines_returns_only_fresh_content(fake_world):
    _write(fake_world["log_file"], "first\nsecond\n")

    assert monitor.read_new_lines() == ["first", "second"]
    # Nothing new appended -> next scan sees nothing.
    assert monitor.read_new_lines() == []

    with open(fake_world["log_file"], "a", encoding="utf-8") as f:
        f.write("third\n")
    assert monitor.read_new_lines() == ["third"]


def test_offset_resets_after_log_truncation(fake_world):
    _write(fake_world["log_file"],
           "one\ntwo\nthree\nfour\nfive\nsix\nseven\n")   # long history
    monitor.read_new_lines()

    # Truncated far below the stored offset -> tail restarts from zero.
    _write(fake_world["log_file"], "fresh\n")
    assert monitor.read_new_lines() == ["fresh"]


def test_partially_written_line_is_not_consumed_early(fake_world):
    _write(fake_world["log_file"], "ERROR disk almost full")
    assert monitor.read_new_lines() == []          # no newline yet

    with open(fake_world["log_file"], "a", encoding="utf-8") as f:
        f.write(" - aborting\nINFO healthy\n")
    assert monitor.read_new_lines() == [
        "ERROR disk almost full - aborting", "INFO healthy",
    ]


def test_non_ascii_lines_survive_byte_offsets(fake_world):
    _write(fake_world["log_file"], "INFO résumé ✓ logged\n")

    assert monitor.read_new_lines() == ["INFO résumé ✓ logged"]
    assert monitor.read_new_lines() == []          # offset stayed byte-exact


def test_missing_log_file_is_not_an_error(fake_world):
    assert monitor.read_new_lines() == []


def test_latest_state_reads_json_or_none(fake_world):
    fake_world["state_file"].parent.mkdir(parents=True, exist_ok=True)
    _write(fake_world["state_file"], json.dumps({"running": True}))

    assert monitor.latest_state() == {"running": True}

    fake_world["state_file"].unlink()
    assert monitor.latest_state() is None
