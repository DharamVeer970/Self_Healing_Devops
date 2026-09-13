"""
SCRIPT-WRITER AGENT - the self-healing agent writes its own fixes.

When the rule-based agent escalates, this node:

  1. NEVER touches tracked source code automatically - and is hard-disabled
     on GitHub Actions runners. A CI run must never produce a dirty repo
     or rewrite your .py files.
  2. The AGENT (LLM) generates a CUSTOM fix script for ANY issue type
     (logging gaps included) and writes it to fixes/ for human review.
  3. Direct source patching exists ONLY as a local opt-in:
     set SCRIPT_PATCH_SOURCE=1 to let the agent edit .py files directly.

Env vars (loaded from .env by agent/__init__.py):
    SCRIPT_FIX_DIR          directory for generated fix scripts (default: <repo>/fixes)
    SCRIPT_DRY_RUN          if "1", only show what WOULD be written (default: "0")
    SCRIPT_PATCH_SOURCE     if "1", allow direct source-file patching locally
                            (default: "0" - OFF everywhere, forced OFF in CI)
    ALLOW_SOURCE_PATCH_IN_CI if "1", overrides the CI guard for sandbox jobs
"""
import json
import os
import re
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX_DIR = os.path.join(BASE_DIR, "fixes")


# ---------------------------------------------------------------------------
# Strategy 1: Direct source code patching (for known patterns)
# ---------------------------------------------------------------------------

def _find_python_files():
    """Return all .py files in the repo (excluding fixes/, cache, etc.)."""
    result = []
    skip = {"fixes", "__pycache__", ".git", ".pytest_cache", "logs",
            "tests", ".github", ".venv", "venv", "node_modules"}
    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".py"):
                result.append(os.path.join(root, f))
    return result


def _file_has_logging(filepath):
    """Return True if the file already has a logging setup block.

    Matches either an explicit `import logging` / logger assignment, or an
    auto-added logging block injected by a previous patch run (the block
    header is missing when this function was patched in after the block).
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return False
    return bool(re.search(r"^[\s]*(import logging|from logging|logger\s*=|_logger\s*=|_log_path\s*=|auto-added logging)", content, re.M))


def _cleanup_injected_logging(filepath):
    """Remove a previously injected auto-added logging block.

    Returns (removed: bool, message: str).
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return False, f"Cannot read {filepath}: {exc}"

    pattern = re.compile(
        r"\n?# --- auto-added logging \(.*?\) ---\n"
        r".*?"
        r"# --- end auto-added logging ---\n?",
        re.DOTALL,
    )
    if not pattern.search(text):
        return False, f"{os.path.basename(filepath)} has no injected block"

    new_text = pattern.sub("\n", text)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_text)
        return True, f"Removed injected logging block from {os.path.basename(filepath)}"
    except OSError as exc:
        return False, f"Cannot write {filepath}: {exc}"


def _skip_leading_blank(lines, start):
    """Advance past blank lines from start; return new index."""
    i = start
    n = len(lines)
    while i < n and not lines[i].strip():
        i += 1
    return i


def _skip_docstring(lines, start):
    """If lines[start] opens a module docstring, skip the whole block."""
    n = len(lines)
    if start >= n:
        return start
    opening = lines[start].lstrip()
    delim = None
    if opening.startswith('"""'):
        delim = '"""'
    elif opening.startswith("'''"):
        delim = "'''"
    if delim is None:
        return start
    # Single-line docstring: """text"""
    if delim in opening[3:]:
        return start + 1
    # Multi-line: scan to closing delimiter
    i = start + 1
    while i < n and delim not in lines[i]:
        i += 1
    return i + 1


def _find_insert_at(lines):
    """Return the line index where an import-time block should be inserted.

    Skips a leading module docstring (so we never inject inside it) and any
    blank lines / comments / import statements that follow it. If the whole
    file is imports, appends at EOF.
    """
    n = len(lines)
    i = _skip_leading_blank(lines, 0)
    i = _skip_docstring(lines, i)

    # Find first non-import / non-comment line after the header.
    for j in range(i, n):
        stripped = lines[j].strip()
        if stripped and not stripped.startswith(("import ", "from ", "#")):
            return j
    return n


def _add_logging_to_file(filepath):
    """OFFLINE fallback: insert a deterministic logging setup block.

    The PRIMARY logging fix is LLM-authored (_apply_llm_logging_patch) so the
    agent writes context-aware code. This deterministic block only runs when
    no LLM key is configured - keeping the self-healing agent usable offline.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as exc:
        return False, f"Cannot read {filepath}: {exc}"

    if _file_has_logging(filepath):
        return False, f"{os.path.basename(filepath)} already has logging"

    insert_at = _find_insert_at(lines)

    name = os.path.splitext(os.path.basename(filepath))[0]
    block = [
        "\n",
        f"# --- auto-added logging ({datetime.now():%Y-%m-%d %H:%M}) ---\n",
        "import logging, os, sys\n",
        "_log_path = os.path.join(os.path.dirname(__file__), '..', 'logs', 'app.log')\n",
        "os.makedirs(os.path.dirname(_log_path), exist_ok=True)\n",
        f"_logger = logging.getLogger('{name}')\n",
        "_logger.setLevel(logging.DEBUG)\n",
        "if not _logger.handlers:\n",
        "    _fh = logging.FileHandler(_log_path, encoding='utf-8')\n",
        "    _fh.setFormatter(logging.Formatter(\n",
        "        '%(asctime)s | %(name)s | %(levelname)s | %(message)s'))\n",
        "    _logger.addHandler(_fh)\n",
        "# --- end auto-added logging ---\n",
        "\n",
    ]

    new_lines = lines[:insert_at] + block + lines[insert_at:]

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True, f"Added logging to {os.path.basename(filepath)}"
    except OSError as exc:
        return False, f"Cannot write {filepath}: {exc}"


def _apply_llm_logging_patch(filepath, patch_code, _error_lines=None):
    """Splice an LLM-authored logging snippet into filepath after the imports.

    The snippet is wrapped in the same auto-added markers used by the
    offline patcher so `_cleanup_injected_logging` can remove it later.
    _error_lines is kept for API compatibility but unused (patch is from LLM).

    Returns (applied: bool, message: str).
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as exc:
        return False, f"Cannot read {filepath}: {exc}"

    if _file_has_logging(filepath):
        return False, f"{os.path.basename(filepath)} already has logging"

    insert_at = _find_insert_at(lines)
    banner = f"# --- auto-added logging ({datetime.now():%Y-%m-%d %H:%M}) ---\n"
    footer = "# --- end auto-added logging ---\n"
    block = ["\n", banner, patch_code.rstrip("\n") + "\n", footer, "\n"]
    new_lines = lines[:insert_at] + block + lines[insert_at:]

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True, f"Agent wrote logging into {os.path.basename(filepath)}"
    except OSError as exc:
        return False, f"Cannot write {filepath}: {exc}"


def _patch_logging_gaps():
    """Scan all .py files and add logging to those that don't have it."""
    patched = []
    for fp in _find_python_files():
        if not _file_has_logging(fp):
            ok, msg = _add_logging_to_file(fp)
            if ok:
                patched.append(msg)
    return patched


# ---------------------------------------------------------------------------
# Strategy 2: LLM generates a CUSTOM fix script for ANY issue
# ---------------------------------------------------------------------------

def _resolve_fix_dir():
    d = os.environ.get("SCRIPT_FIX_DIR", FIX_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def _is_dry_run():
    return os.environ.get("SCRIPT_DRY_RUN", "0").strip().lower() in ("1", "true", "yes")


def _in_ci():
    """Are we running under GitHub Actions (or any standard CI runner)?"""
    return bool(os.environ.get("GITHUB_ACTIONS")) or os.environ.get(
        "CI", "").strip().lower() in ("1", "true", "yes")


def _source_patching_allowed():
    """Direct source mutation requires an explicit LOCAL opt-in.

    Your GitHub workflows auto-trigger the agent; on those runners it must
    NEVER rewrite tracked source files (that would dirty the repo and break
    the build). So patching is OFF by default everywhere, and even setting
    SCRIPT_PATCH_SOURCE=1 does not enable it inside GitHub Actions unless
    ALLOW_SOURCE_PATCH_IN_CI=1 too (for throwaway sandbox jobs).
    """
    if os.environ.get("SCRIPT_PATCH_SOURCE", "0").strip().lower() not in (
            "1", "true", "yes"):
        return False
    if _in_ci() and os.environ.get("ALLOW_SOURCE_PATCH_IN_CI", "0") != "1":
        return False
    return True


def _llm_call(system, user, max_tokens=800, temperature=0.2):
    """One OpenAI-compatible chat completion (TokenRouter / OpenRouter).

    Returns the message content string, or None when no LLM key is
    configured or the call fails - the agent degrades gracefully instead
    of ever crashing on network problems.
    """
    from agent import diagnose as _diag
    api_key, url, model = _diag.llm_config()
    if not api_key:
        return None

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode("utf-8")

    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    if "openrouter.ai" in url:
        headers["HTTP-Referer"] = "http://localhost:self-healing-agent"
        headers["X-Title"] = "Self-Healing DevOps Agent"

    try:
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _llm_generate_fix(error_lines, diagnosis):
    """Use the LLM to generate a custom fix script for ANY issue type.

    Returns the script content as a string, or None if LLM is unavailable.
    """
    prompt = (
        "You are a senior SRE. The following errors were detected:\n"
        + "\n".join(error_lines[-10:])
        + f"\n\nAutomated diagnosis: {diagnosis['diagnosis']} "
        f"(proposed action: {diagnosis['action']}, confidence: {diagnosis['confidence']}).\n\n"
        "Write a COMPLETE, self-contained Python script that fixes this issue.\n"
        "The script MUST:\n"
        "  1. Be idempotent (safe to run multiple times)\n"
        "  2. Include comments explaining each step\n"
        "  3. Print what it's doing at each step\n"
        "  4. Handle errors gracefully\n"
        "Output ONLY the Python script content (no markdown, no explanation outside the script)."
    )
    return _llm_call(
        "You are a senior SRE who writes safe, idempotent Python fix scripts.",
        prompt,
    )


def _llm_generate_logging_patch(filepath, error_lines):
    """THE AGENT writes the logging patch: it reads the actual failing file
    plus the monitor error, and generates a context-aware logging snippet.

    Returns (patch_code: str | None, message: str). patch_code is the raw
    Python to splice after the file's imports; None means the LLM was
    unavailable or produced something unusable.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError as exc:
        return None, f"Cannot read {filepath}: {exc}"

    context = "\n".join(error_lines[-3:]) if error_lines else "(no error context)"
    prompt = (
        "A DevOps monitor could not see what a Python module is doing "
        "because the module emits NO logs. Write a logging setup block.\n\n"
        f"Error context from the monitor:\n{context}\n\n"
        "File to patch (first 60 lines):\n"
        + "\n".join(source.splitlines()[:60]) + "\n\n"
        "Write ONLY a Python code block that sets up a module logger named "
        "'_logger' which:\n"
        "  - writes to <project_root>/logs/app.log (UTF-8) via logging.FileHandler\n"
        "  - sets level DEBUG\n"
        "  - is idempotent (guarded by `if not _logger.handlers:` so it "
        "never double-registers)\n"
        "The block must be valid Python that can be INSERTED right after the "
        "imports. Output only the code - no markdown fences, no explanation."
    )
    code = _llm_call(
        "You are an expert Python engineer. You output exact, safe code "
        "insertions and nothing else.",
        prompt,
        max_tokens=500,
    )
    if not code:
        return None, "LLM unavailable or call failed - using offline patch"
    # Strip accidental markdown fences with plain string slicing.
    code = code.strip()
    if code.startswith("```"):
        code = code[3:]
        if code[:6].lower() == "python":
            code = code[6:]
        code = code.lstrip()
    if code.endswith("```"):
        code = code[:-3]
    code = code.strip()
    if "logger" not in code.lower() and "logging" not in code.lower():
        return None, "LLM output did not look like a logging setup - skipped"
    return code, "Agent generated logging patch from file context"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _is_logging_issue(error_lines):
    """Check if error text suggests a logging gap."""
    error_text = " ".join(error_lines[-5:]) if error_lines else ""
    low = error_text.lower()
    return "log" in low or "logging" in low


def _collect_logging_patches(error_lines):
    """Apply LLM + offline logging patches; return list of messages or []."""
    applied = []
    for fp in _find_python_files():
        if _file_has_logging(fp):
            continue
        code, _ = _llm_generate_logging_patch(fp, error_lines)
        if code:
            ok, msg = _apply_llm_logging_patch(fp, code, error_lines)
            if ok:
                applied.append(msg)
    offline = _patch_logging_gaps()
    return applied + offline


def _sanitize_action(action):
    """Make action safe for filenames (explicit grouping, no backtracking risk)."""
    # Simple char class – linear, no super-linear backtracking (S8786 fix).
    safe = re.sub(r"[^a-z0-9]+", "_", action.lower())
    return safe.strip("_") or "fix"


def _build_fix_content(diagnosis, action, conf, llm_content):
    """Build header + LLM content for a fix script."""
    header = (
        '"""\nAuto-generated fix for: ' + diagnosis["diagnosis"] + "\n"
        f"Action: {action} | Confidence: {conf}\n"
        "Review before running. "
        f"Generated at {datetime.now():%Y-%m-%d %H:%M:%S}.\n" + '"""' + "\n\n"
    )
    return header + llm_content


def _attempt_source_patching(error_lines):
    """Try Strategy 1: direct source logging patch (opt-in only)."""
    if not _source_patching_allowed() or not _is_logging_issue(error_lines):
        return None
    patches = _collect_logging_patches(error_lines)
    if patches:
        return {
            "strategy": "patch_source_logging",
            "patches": patches,
            "needs_review": False,
            "message": "Agent wrote logging into files that had none. Review the changes.",
        }
    return None


def _attempt_llm_script(error_lines, diagnosis):
    """Try Strategy 2: LLM generates a custom fix script."""
    llm_content = _llm_generate_fix(error_lines, diagnosis)
    if not llm_content:
        return None
    action = diagnosis.get("action", "")
    conf = diagnosis.get("confidence", 0)
    safe_action = _sanitize_action(action)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"llm_fix_{safe_action}_{ts}.py"
    content = _build_fix_content(diagnosis, action, conf, llm_content)

    if _is_dry_run():
        fix_dir = os.environ.get("SCRIPT_FIX_DIR", FIX_DIR)
        filepath = os.path.join(fix_dir, filename)
        return {
            "strategy": "llm_script_dry_run",
            "filepath": filepath,
            "content": content,
            "needs_review": True,
            "message": f"[DRY RUN] Would write fix script to {filename}",
        }

    fix_dir = _resolve_fix_dir()
    filepath = os.path.join(fix_dir, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return {
            "strategy": "llm_script",
            "filepath": filepath,
            "content": content,
            "needs_review": True,
            "message": f"Fix script written to {filename} - review before running",
        }
    except OSError as exc:
        return {
            "strategy": "none",
            "needs_review": True,
            "message": f"Could not write fix script: {exc}",
        }


def attempt_fix(error_lines, diagnosis):
    """
    Called when the rule-based agent escalates to human.

    Routing (CI-safe, never auto-mutates source):
      1. Direct source patching ONLY when explicitly opted in locally
         (SCRIPT_PATCH_SOURCE=1 and not running in GitHub Actions).
      2. Otherwise the AGENT (LLM) writes a CUSTOM fix script for ANY issue
         type - "no logs showing" included - to fixes/ for human review.

    Returns a dict:
        {"strategy": ..., "patches"/"filepath": ..., "content": ...,
         "needs_review": bool, "message": ...}
    """
    patched = _attempt_source_patching(error_lines)
    if patched:
        return patched

    llm_result = _attempt_llm_script(error_lines, diagnosis)
    if llm_result:
        return llm_result

    return {
        "strategy": "none",
        "needs_review": True,
        "message": "No LLM key configured and no source patches applicable - human intervention required",
    }
