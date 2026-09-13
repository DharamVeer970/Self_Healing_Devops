"""Monitor node: binary-safe tailing, offsets, extraction, state reading."""
import json
import os

import pytest

from agent import monitor
from conftest import read, write


# ---- _read_offset ----------------------------------------------------------



def test_read_offset_missing_file_is_zero(sandbox):
    assert monitor._read_offset() == 0


def test_read_offset_invalid_content_is_zero(sandbox):
    write(sandbox["offset_file"], "not-a-number")
    assert monitor._read_offset() == 0


def test_read_offset_negative_string_is_zero(sandbox):
    write(sandbox["offset_file"], "-5")
    assert monitor._read_offset() == 0


def test_read_offset_valid(sandbox):
    write(sandbox["offset_file"], "  42  ")
    assert monitor._read_offset() == 42


def test_read_offset_whitespace_only(sandbox):
    write(sandbox["offset_file"], "   ")
    assert monitor._read_offset() == 0


# ---- _write_offset ---------------------------------------------------------

def test_write_offset_creates_dir_and_file(sandbox):
    target = sandbox["logs"] / "sub" / ".offset"
    monitor.OFFSET_FILE = str(target)  # not in fixture dir, must be created
    monitor._write_offset(7)
    assert read(target) == "7"


def test_write_offset_roundtrip(sandbox):
    monitor._write_offset(123)
    assert monitor._read_offset() == 123


# ---- read_new_lines --------------------------------------------------------

def test_missing_log_file_returns_empty(sandbox):
    assert monitor.read_new_lines() == []


def test_empty_log_file_returns_empty(sandbox):
    write(sandbox["log_file"], "")
    assert monitor.read_new_lines() == []


def test_reads_all_lines_first_scan(sandbox):
    write(sandbox["log_file"], "a\nb\nc\n")
    assert monitor.read_new_lines() == ["a", "b", "c"]


def test_second_scan_returns_nothing(sandbox):
    write(sandbox["log_file"], "one\ntwo\n")
    assert monitor.read_new_lines() == ["one", "two"]
    assert monitor.read_new_lines() == []


def test_only_returns_new_lines_after_append(sandbox):
    write(sandbox["log_file"], "one\n")
    monitor.read_new_lines()
    with open(sandbox["log_file"], "a", encoding="utf-8") as f:
        f.write("two\nthree\n")
    assert monitor.read_new_lines() == ["two", "three"]


def test_trailing_line_without_newline_not_consumed(sandbox):
    write(sandbox["log_file"], "partial")
    assert monitor.read_new_lines() == []          # no newline yet
    with open(sandbox["log_file"], "a", encoding="utf-8") as f:
        f.write(" completes\n")
    assert monitor.read_new_lines() == ["partial completes"]


def test_trailing_newline_partial_is_held(sandbox):
    write(sandbox["log_file"], "complete line\nincomplete")
    assert monitor.read_new_lines() == ["complete line"]
    # the partial remains; appending rest completes it:
    with open(sandbox["log_file"], "a", encoding="utf-8") as f:
        f.write(" now\n")
    assert monitor.read_new_lines() == ["incomplete now"]


def test_truncation_resets_offset(sandbox):
    write(sandbox["log_file"], "one\ntwo\nthree\nfour\nfive\nsix\nseven\n")
    monitor.read_new_lines()
    # Truncate far below stored offset (as log rotation would).
    write(sandbox["log_file"], "fresh\n")
    assert monitor.read_new_lines() == ["fresh"]


def test_non_ascii_multibyte_lines(sandbox):
    write(sandbox["log_file"], "INFO r\u00e9sum\u00e9 \u2713 ok\n")
    assert monitor.read_new_lines() == ["INFO r\u00e9sum\u00e9 \u2713 ok"]
    assert monitor.read_new_lines() == []          # byte offset stayed clean


def test_crlf_line_endings(sandbox):
    write(sandbox["log_file"], "ERROR one\r\nERROR two\r\n")
    assert monitor.read_new_lines() == ["ERROR one", "ERROR two"]


def test_invalid_utf8_bytes_decoded_without_crash(sandbox):
    # 0xFF/0xFE are invalid UTF-8; errors="replace" must never raise.
    with open(sandbox["log_file"], "wb") as f:
        f.write(b"ERROR \xff\xfe line\n")
    lines = monitor.read_new_lines()
    assert len(lines) == 1
    assert lines[0].startswith("ERROR ")
    assert "\ufffd" in lines[0]                   # replaced, not raised


# ---- extract_errors --------------------------------------------------------

def test_extract_keeps_error_and_fatal(sandbox):
    lines = ["INFO ok", "ERROR bad", "FATAL worse", "WARN noise",
             "DEBUG detail"]
    assert monitor.extract_errors(lines) == ["ERROR bad", "FATAL worse"]


def test_extract_is_case_sensitive_uppercase(sandbox):
    # Documented behavior: only uppercase ERROR/FATAL are considered.
    assert monitor.extract_errors(["error lower", "Error mixed"]) == []


def test_extract_empty(sandbox):
    assert monitor.extract_errors([]) == []


def test_extract_error_within_longer_word(sandbox):
    assert monitor.extract_errors(["X_ERROR_Y"]) == ["X_ERROR_Y"]


# ---- latest_state ----------------------------------------------------------

def test_latest_state_missing_returns_none(sandbox):
    assert monitor.latest_state() is None


def test_latest_state_reads_json(sandbox):
    write(sandbox["state_file"], json.dumps({"running": True}))
    assert monitor.latest_state() == {"running": True}


def test_latest_state_malformed_raises(sandbox):
    write(sandbox["state_file"], "{ not valid json")
    with pytest.raises(json.JSONDecodeError):
        monitor.latest_state()
