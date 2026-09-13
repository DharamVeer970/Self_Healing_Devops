"""Reporting node: report formatting + emit (print/deliver)."""
from unittest import mock

import pytest

from agent import reporting



DIAG = {"error_line": "ERROR disk full -" + "x" * 200,
        "diagnosis": "Disk is full.",
        "confidence": 0.97, "action": "rotate_logs"}


# ---- format_report ---------------------------------------------------------

def test_format_report_has_core_fields():
    out = reporting.format_report(["err"], DIAG)
    for token in ("INCIDENT REPORT", "Disk is full.", "97%",
                  "rotate_logs", "Rotate/compress"):
        assert token in out


def test_error_line_truncated_to_100():
    out = reporting.format_report(["err"], DIAG)
    line = [ln for ln in out.split("\n") if "Error captured" in ln][0]
    assert len(line.split(":", 1)[1].strip()) == 100


def test_outcome_included_when_present():
    out = reporting.format_report(["err"], DIAG, outcome="Fixed it")
    assert "Action taken   : Fixed it" in out


def test_outcome_omitted_when_absent():
    out = reporting.format_report(["err"], DIAG)
    assert "Action taken" not in out


def test_healthy_verification():
    out = reporting.format_report(["err"], DIAG,
                                  verification=(True, [], {"a": 1}))
    assert "HEALTHY" in out
    assert "Machine state  : {'a': 1}" in out
    assert "BROKEN" not in out


def test_broken_verification_lists_problems():
    out = reporting.format_report(["err"], DIAG,
                                  verification=(False, ["down", "disk"], {}))
    assert "BROKEN" in out
    assert "down" in out and "disk" in out


def test_verification_omitted_when_none():
    out = reporting.format_report(["err"], DIAG)
    assert "Verification" not in out
    assert "Machine state" not in out


def test_llm_text_split_into_indented_lines():
    out = reporting.format_report(["err"], DIAG, llm_text="line1\nline2")
    assert " LLM root-cause analysis:" in out
    assert "   line1" in out
    assert "   line2" in out


def test_llm_text_omitted_when_none():
    out = reporting.format_report(["err"], DIAG)
    assert "LLM root-cause analysis" not in out


# ---- emit ------------------------------------------------------------------

def test_emit_prints_report(capsys):
    with mock.patch.object(reporting.notify, "deliver", return_value={}):
        reporting.emit(["err"], DIAG)
    assert "INCIDENT REPORT" in capsys.readouterr().out


def test_emit_calls_deliver_with_title_and_body():
    with mock.patch.object(reporting.notify, "deliver",
                           return_value={}) as deliver:
        reporting.emit(["err"], DIAG, outcome="ok")
    assert deliver.call_count == 1
    title = deliver.call_args.kwargs["title"]
    body = deliver.call_args.kwargs["body"]
    assert title == "[Self-Healing Agent] rotate_logs"
    assert "error" in body and "ok" in body


def test_emit_prints_delivery_status_when_deliveries(capsys):
    with mock.patch.object(reporting.notify, "deliver",
                           return_value={"slack": "sent"}):
        reporting.emit(["err"], DIAG)
    assert "Delivered     : slack: sent" in capsys.readouterr().out


def test_emit_no_delivery_status_when_none(capsys):
    with mock.patch.object(reporting.notify, "deliver", return_value={}):
        reporting.emit(["err"], DIAG)
    assert "Delivered" not in capsys.readouterr().out


def test_emit_includes_outcome_and_verified_in_body():
    with mock.patch.object(reporting.notify, "deliver",
                           return_value={}) as deliver:
        reporting.emit(["err"], DIAG, outcome="fixed",
                       verification=(True, [], {}))
    body = deliver.call_args.kwargs["body"]
    assert "fixed" in body
    assert "HEALTHY" in body


def test_emit_without_verification_no_verified_field():
    with mock.patch.object(reporting.notify, "deliver",
                           return_value={}) as deliver:
        reporting.emit(["err"], DIAG)
    body = deliver.call_args.kwargs["body"]
    assert "verified" not in body


def test_emit_with_broken_verification_lists_problems():
    with mock.patch.object(reporting.notify, "deliver",
                           return_value={}) as deliver:
        reporting.emit(["err"], DIAG,
                       verification=(False, ["disk still full"], {}))
    body = deliver.call_args.kwargs["body"]
    assert "['disk still full']" in body
