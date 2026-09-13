"""Script-writer agent: logging patcher + LLM custom fix generation."""
import os

from agent import script_writer


# ---- _file_has_logging ------------------------------------------------------

def test_file_without_logging(tmp_path):
    p = tmp_path / "plain.py"
    p.write_text("import os\n\nx = 1\n", encoding="utf-8")
    assert script_writer._file_has_logging(str(p)) is False


def test_file_with_logging_import(tmp_path):
    p = tmp_path / "logged.py"
    p.write_text("import logging\nlogger = logging.getLogger('x')\n",
                 encoding="utf-8")
    assert script_writer._file_has_logging(str(p)) is True


def test_file_with_from_logging(tmp_path):
    p = tmp_path / "fromlog.py"
    p.write_text("from logging import getLogger\n", encoding="utf-8")
    assert script_writer._file_has_logging(str(p)) is True


def test_missing_file_has_no_logging(tmp_path):
    assert script_writer._file_has_logging(
        str(tmp_path / "nope.py")) is False


# ---- _add_logging_to_file ---------------------------------------------------

def test_add_logging_success(tmp_path):
    p = tmp_path / "svc.py"
    p.write_text('"""Service."""\nimport os\n\n\ndef run():\n    pass\n',
                 encoding="utf-8")
    ok, msg = script_writer._add_logging_to_file(str(p))
    assert ok is True
    text = p.read_text(encoding="utf-8")
    # Block must be real code AFTER the docstring, not inside it.
    assert text.index('"""Service."""') < text.index("auto-added logging")
    assert "_logger = logging.getLogger('svc')" in text
    assert "Service." in text  # docstring preserved


def test_add_logging_skips_if_already_present(tmp_path):
    p = tmp_path / "has.py"
    p.write_text("import logging\n", encoding="utf-8")
    ok, _ = script_writer._add_logging_to_file(str(p))
    assert ok is False


def test_add_logging_handles_read_error(tmp_path):
    ok, msg = script_writer._add_logging_to_file(
        str(tmp_path / "does_not_exist.py"))
    assert ok is False
    assert "Cannot read" in msg

# ---------------------------------------------------------------------------
# attempt_fix routing (strategy 1 logging patch -> strategy 2 LLM -> none)
# ---------------------------------------------------------------------------

def _diag(action="escalate_to_human"):
    return {"action": action, "diagnosis": "D", "confidence": 0.0,
            "error_line": "E"}


def test_attempt_fix_patches_logging_gaps(monkeypatch, tmp_path):
    """Logging-related errors trigger a direct source patch (when opted in)."""
    target = tmp_path / "svc.py"
    target.write_text('"""S."""\nimport os\n\ndef run():\n    pass\n',
                      encoding="utf-8")
    monkeypatch.setattr(script_writer, "_find_python_files",
                        lambda: [str(target)])
    monkeypatch.setattr(script_writer, "_source_patching_allowed",
                        lambda: True)
    result = script_writer.attempt_fix(["ERROR no logs showing"], _diag())
    assert result["strategy"] == "patch_source_logging"
    assert result["needs_review"] is False
    assert "logging" in target.read_text(encoding="utf-8")


def test_attempt_fix_llm_script_for_connectivity(monkeypatch, tmp_path):
    """Connectivity DOWNs produce a custom LLM fix script for human review."""
    monkeypatch.setenv("SCRIPT_FIX_DIR", str(tmp_path))
    monkeypatch.setattr(script_writer, "_llm_generate_fix",
                        lambda e, d: "print('fix connectivity')\n")
    errors = ["ERROR connectivity: https://x.onrender.com is DOWN (timeout)"]
    result = script_writer.attempt_fix(errors, _diag())
    assert result["strategy"] == "llm_script"
    assert result["needs_review"] is True
    assert os.path.exists(result["filepath"])
    body = open(result["filepath"], encoding="utf-8").read()
    assert "fix connectivity" in body


def test_attempt_fix_llm_for_unknown_action(monkeypatch, tmp_path):
    """Any unknown issue type gets a custom LLM script (no templates)."""
    monkeypatch.setenv("SCRIPT_FIX_DIR", str(tmp_path))
    monkeypatch.setattr(script_writer, "_llm_generate_fix",
                        lambda e, d: "# custom fix\n")
    result = script_writer.attempt_fix(["ERROR something weird"],
                                       _diag(action="weird_thing"))
    assert result["strategy"] == "llm_script"
    assert result["filepath"].endswith(".py")


def test_attempt_fix_none_without_llm(monkeypatch):
    """No LLM key + no logging gap -> honest 'none' escalation."""
    monkeypatch.setattr(script_writer, "_llm_generate_fix",
                        lambda e, d: None)
    monkeypatch.setattr(script_writer, "_patch_logging_gaps",
                        lambda: [])
    result = script_writer.attempt_fix(["ERROR disk exploded"], _diag())
    assert result["strategy"] == "none"
    assert result["needs_review"] is True


def test_llm_returns_none_without_key(monkeypatch):
    """LLM strategy degrades cleanly when no key is configured."""
    from agent import diagnose as _d
    monkeypatch.setattr(_d, "llm_config", lambda: (None, "", ""))
    assert script_writer._llm_generate_fix(["e"], _diag()) is None


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    """Dry-run returns the script content without touching the filesystem."""
    monkeypatch.setenv("SCRIPT_DRY_RUN", "1")
    monkeypatch.setenv("SCRIPT_FIX_DIR", str(tmp_path / "fixes"))
    monkeypatch.setattr(script_writer, "_llm_generate_fix",
                        lambda e, d: "print('x')\n")
    result = script_writer.attempt_fix(["ERROR boom"], _diag())
    assert result["strategy"] == "llm_script_dry_run"
    assert result["needs_review"] is True
    assert not (tmp_path / "fixes").exists()



# ---- _resolve_fix_dir / _is_dry_run ------------------------------------------

def test_fix_dir_default(monkeypatch):
    monkeypatch.delenv("SCRIPT_FIX_DIR", raising=False)
    assert script_writer._resolve_fix_dir() == script_writer.FIX_DIR


def test_fix_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRIPT_FIX_DIR", str(tmp_path / "fixes"))
    assert script_writer._resolve_fix_dir() == str(tmp_path / "fixes")


def test_dry_run_off_by_default():
    assert script_writer._is_dry_run() is False


def test_dry_run_enabled(monkeypatch):
    monkeypatch.setenv("SCRIPT_DRY_RUN", "1")
    assert script_writer._is_dry_run() is True


# ---- attempt_fix ------------------------------------------------------------


def test_attempt_fix_patches_logging(monkeypatch, tmp_path):
    """Logging-gap errors auto-patch source files (when opted in)."""
    target = tmp_path / "svc.py"
    target.write_text("import os\n\nX = 1\n", encoding="utf-8")
    monkeypatch.setattr(script_writer, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(script_writer, "FIX_DIR", str(tmp_path / "fixes"))
    monkeypatch.setattr(script_writer, "_source_patching_allowed",
                        lambda: True)
    result = script_writer.attempt_fix(
        ["ERROR logging not configured", "no logs showing"],
        {"diagnosis": "logs missing", "action": "fix_logging", "confidence": 0.3},
    )
    assert result["strategy"] == "patch_source_logging"
    assert "_logger" in target.read_text(encoding="utf-8")


def test_attempt_fix_llm_when_available(monkeypatch, tmp_path):
    """LLM generates a custom fix script when no source patch applies."""
    monkeypatch.setattr(script_writer, "_llm_generate_fix",
                        lambda e, d: "print('custom-fix')")
    monkeypatch.setattr(script_writer, "_resolve_fix_dir",
                        lambda: str(tmp_path))
    result = script_writer.attempt_fix(
        ["ERROR Connection refused"],
        {"diagnosis": "backend down", "action": "restart_service",
         "confidence": 0.5},
    )
    assert result["strategy"] == "llm_script"
    assert (tmp_path / result["filepath"].split("/")[-1]
            ).read_text(encoding="utf-8").find("custom-fix") >= 0


def test_attempt_fix_llm_dry_run(monkeypatch, tmp_path):
    """Dry-run mode -> strategy llm_script_dry_run, nothing written."""
    monkeypatch.setenv("SCRIPT_FIX_DIR", str(tmp_path / "fixes"))
    monkeypatch.setenv("SCRIPT_DRY_RUN", "1")
    monkeypatch.setattr(script_writer, "_llm_generate_fix",
                        lambda e, d: "print('fix')\n")
    result = script_writer.attempt_fix(
        ["ERROR disk exploded"],
        {"diagnosis": "?", "action": "escalate_to_human", "confidence": 0.0},
    )
    assert result["strategy"] == "llm_script_dry_run"
    assert result["needs_review"] is True
    assert not os.path.isdir(tmp_path / "fixes")


def test_llm_returns_none_without_key(monkeypatch):
    """_llm_generate_fix returns None when no LLM key is configured."""
    from agent import diagnose
    monkeypatch.setattr(diagnose, "llm_config", lambda: ("", "url", "m"))
    assert script_writer._llm_generate_fix(
        ["ERROR x"],
        {"diagnosis": "?", "action": "escalate_to_human", "confidence": 0.0},
    ) is None


def test_attempt_fix_none_without_llm(monkeypatch, tmp_path):
    """No LLM + no patchable source -> human escalation."""
    monkeypatch.setattr(script_writer, "_llm_generate_fix",
                        lambda e, d: None)
    monkeypatch.setattr(script_writer, "_patch_logging_gaps",
                        lambda: [])
    result = script_writer.attempt_fix(
        ["ERROR unknown thing"],
        {"diagnosis": "?", "action": "escalate_to_human", "confidence": 0.0},
    )
    assert result["strategy"] == "none"
    assert result["needs_review"] is True


# ---------------------------------------------------------------------------
# LLM-authored logging patches (the agent writes the fix, not a template)
# ---------------------------------------------------------------------------

def test_attempt_fix_llm_authored_logging_patch(monkeypatch, tmp_path):
    """When the LLM is available, attempt_fix uses the agent-authored patch."""
    target = tmp_path / "svc.py"
    target.write_text('"""S."""\nimport os\n\ndef run():\n    pass\n',
                      encoding="utf-8")
    monkeypatch.setattr(script_writer, "_find_python_files",
                        lambda: [str(target)])
    monkeypatch.setattr(script_writer, "_source_patching_allowed",
                        lambda: True)
    llm_code = ("_logger = logging.getLogger('svc')\n"
                "if not _logger.handlers:\n"
                "    _logger.addHandler(\n"
                "        logging.FileHandler('../logs/app.log', encoding='utf-8'))\n")
    monkeypatch.setattr(script_writer, "_llm_generate_logging_patch",
                        lambda fp, err: (llm_code, "ok"))
    result = script_writer.attempt_fix(["ERROR no logs showing"], _diag())
    assert result["strategy"] == "patch_source_logging"
    text = target.read_text(encoding="utf-8")
    assert "auto-added logging" in text          # wrapped in markers
    assert "getLogger('svc')" in text            # agent's code landed
    # Patch sits AFTER the docstring (live code, not docstring text).
    assert text.index('"""S."""') < text.index("getLogger")


def test_llm_generate_logging_patch_no_key(monkeypatch, tmp_path):
    """No LLM key -> returns (None, offline-msg), used as fallback trigger."""
    from agent import diagnose
    monkeypatch.setattr(diagnose, "llm_config", lambda: ("", "url", "m"))
    p = tmp_path / "a.py"
    p.write_text("import os\n", encoding="utf-8")
    code, msg = script_writer._llm_generate_logging_patch(str(p), ["ERROR logs"])
    assert code is None
    assert "offline" in msg.lower()


def test_llm_generate_logging_patch_read_error(tmp_path):
    """Missing file -> (None, Cannot read ...) before any LLM call."""
    code, msg = script_writer._llm_generate_logging_patch(
        str(tmp_path / "nope.py"), [])
    assert code is None
    assert "Cannot read" in msg


def test_llm_generate_logging_patch_rejects_junk(monkeypatch, tmp_path):
    """LLM output that isn't logging-like is rejected (never injected)."""
    p = tmp_path / "a.py"
    p.write_text("import os\n", encoding="utf-8")
    monkeypatch.setattr(script_writer, "_llm_call",
                        lambda s, u, max_tokens=500: "def something():\n    x=1\n")
    code, msg = script_writer._llm_generate_logging_patch(str(p), [])
    assert code is None
    assert "skipped" in msg.lower()


def test_llm_generate_logging_patch_strips_fences(monkeypatch, tmp_path):
    """Markdown fences around the LLM snippet are stripped."""
    p = tmp_path / "a.py"
    p.write_text("import os\n", encoding="utf-8")
    monkeypatch.setattr(script_writer, "_llm_call", lambda s, u, max_tokens=500:
                        "```python\n_logger = logging.getLogger('a')\n```")
    code, msg = script_writer._llm_generate_logging_patch(str(p), [])
    assert code is not None
    assert "```" not in code
    assert "getLogger('a')" in code


def test_apply_llm_logging_patch_places_block(monkeypatch, tmp_path):
    """Applied patch is wrapped in markers and stays after the docstring."""
    p = tmp_path / "svc.py"
    p.write_text('"""Service."""\nimport os\n\nRUN = True\n', encoding="utf-8")
    ok, _ = script_writer._apply_llm_logging_patch(
        str(p), "_logger = logging.getLogger('svc')\n", ["ERROR no logs"])
    assert ok is True
    text = p.read_text(encoding="utf-8")
    assert "auto-added logging" in text
    assert "end auto-added logging" in text
    assert text.index('"""Service."""') < text.index("auto-added logging")


# ---------------------------------------------------------------------------
# CI safety: source patching must be opt-in and impossible in GitHub Actions
# ---------------------------------------------------------------------------

def test_source_patching_off_by_default(monkeypatch):
    monkeypatch.delenv("SCRIPT_PATCH_SOURCE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CI", raising=False)
    assert script_writer._source_patching_allowed() is False


def test_source_patching_disabled_in_ci(monkeypatch):
    """Even with SCRIPT_PATCH_SOURCE=1, GitHub Actions forces it OFF."""
    monkeypatch.setenv("SCRIPT_PATCH_SOURCE", "1")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("ALLOW_SOURCE_PATCH_IN_CI", raising=False)
    assert script_writer._source_patching_allowed() is False


def test_ci_guard_overridden_only_by_explicit_flag(monkeypatch):
    """ALLOW_SOURCE_PATCH_IN_CI=1 re-enables patching for sandbox CI jobs."""
    monkeypatch.setenv("SCRIPT_PATCH_SOURCE", "1")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("ALLOW_SOURCE_PATCH_IN_CI", "1")
    assert script_writer._source_patching_allowed() is True


def test_logging_error_with_patch_off_uses_agent_script(monkeypatch, tmp_path):
    """Default (patching OFF): 'no logs' flows to the LLM fix script, and no
    tracked source file is touched - even in the CI workflow."""
    target = tmp_path / "svc.py"
    target.write_text("import os\n\nX = 1\n", encoding="utf-8")
    monkeypatch.setattr(script_writer, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(script_writer, "FIX_DIR", str(tmp_path / "fixes"))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")   # simulate the workflow
    monkeypatch.setattr(script_writer, "_llm_generate_fix",
                        lambda e, d: "print('add logging here')\n")
    result = script_writer.attempt_fix(
        ["ERROR no logs showing"],
        {"diagnosis": "logs missing", "action": "fix_logging", "confidence": 0.3},
    )
    assert result["strategy"] == "llm_script"      # agent writes a script
    assert "no logs" not in target.read_text(encoding="utf-8")  # source intact
    fix_file = tmp_path / "fixes" / result["filepath"].replace("\\", "/").split("/")[-1]
    assert "print('add logging here')" in fix_file.read_text(encoding="utf-8")
