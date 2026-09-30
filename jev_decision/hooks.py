"""Pre-execution shell guard for agent-harness hooks: escalate-only and fail-open.

A harness hook runs synchronously before a shell command executes, which is where
a fast System 1 check pays off: the primary model spends no tokens deciding to
call a tool, and a flagged command is stopped before it runs. The adapter can
only *add* friction. It never answers "allow", so a harness's own permission
rules, allowlists and prompts stay authoritative. Every failure (no setup, no
key, budget, timeout, malformed input) produces no decision and the harness
proceeds exactly as it would without the hook.

- Harnesses whose hooks can ask (Claude Code, Cursor) receive "ask" for a
  flagged command, which forces the normal approval prompt.
- Harnesses whose hooks can only allow or deny (Command Code, Codex, Gemini CLI)
  receive "deny" with a reason. By default this happens only when the session
  runs without approval prompts (bypass/yolo modes), so the guard never removes
  a person's chance to approve; ``when="always"`` blocks in every mode.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

HOOK_HARNESSES = ("claude-code", "command-code", "codex", "cursor", "gemini-cli")
ASK_CAPABLE = frozenset({"claude-code", "cursor"})
DEFAULT_THRESHOLD = 0.8
HOOK_TIMEOUT_S = 3.0
MAX_HOOK_INPUT_BYTES = 256 * 1024
# permission_mode values meaning no person will approve the command first.
UNATTENDED_MODES = frozenset({"bypass", "bypasspermissions", "dont-ask", "dontask", "yolo"})
_SHELL_TOOLS = {
    "claude-code": {"Bash", "PowerShell"},
    "codex": {"Bash"},
    "command-code": {"shell_command"},
    "gemini-cli": {"run_shell_command"},
}
# Simple read-only commands need no semantic check: skipping them only means the
# harness's native behavior applies, never that anything was approved.
_READ_ONLY = frozenset({"ls", "dir", "pwd", "cat", "head", "tail", "wc", "echo", "which", "where",
                        "whoami", "date", "uname", "tree", "stat", "file", "du", "df", "grep", "rg",
                        "ag", "sort", "uniq", "diff", "cmp", "basename", "dirname", "realpath"})
_READ_ONLY_GIT = frozenset({"status", "log", "diff", "show", "rev-parse", "ls-files", "blame",
                            "describe", "shortlog"})
_SHELL_SYNTAX = re.compile(r"[;&|`$<>(){}\n\r\\*?\[\]~!]")


class HookInputError(ValueError):
    """The hook payload is not a shell command this adapter recognizes."""


def extract_command(harness: str, payload: Any) -> Tuple[str, str]:
    """Return ``(command, cwd)`` from a harness hook payload."""
    if harness not in HOOK_HARNESSES:
        raise HookInputError("unknown_hook_harness")
    if not isinstance(payload, dict):
        raise HookInputError("invalid_hook_payload")
    if harness == "cursor":
        command, cwd = payload.get("command"), payload.get("cwd", "")
    else:
        if payload.get("tool_name") not in _SHELL_TOOLS[harness]:
            raise HookInputError("not_a_shell_command")
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            raise HookInputError("invalid_hook_payload")
        command = tool_input.get("command")
        if isinstance(command, list) and all(isinstance(item, str) for item in command):
            command = shlex.join(command)
        extra = tool_input.get("args")
        if isinstance(command, str) and isinstance(extra, list) and all(isinstance(item, str) for item in extra):
            command = " ".join([command] + [shlex.quote(item) for item in extra])
        cwd = tool_input.get("cwd") or tool_input.get("directory") or payload.get("cwd", "")
    if not isinstance(command, str) or not command.strip():
        raise HookInputError("missing_command")
    return command, cwd if isinstance(cwd, str) else ""


def is_plainly_read_only(command: str) -> bool:
    """Conservatively recognize simple inspection commands that need no check.

    Any shell syntax (pipes, chaining, substitution, redirection, globbing), an
    argument naming a credential/private file, or a path outside the working
    directory (absolute, drive-qualified or climbing with ``..``) disqualifies
    the command, because reading private data is itself a sensitive effect.
    """
    if _SHELL_SYNTAX.search(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if not words:
        return False
    from .evidence_file import _denied

    for word in words[1:]:
        if word.startswith("-"):
            continue
        parts = Path(word).parts
        if (_denied(parts) or ".." in parts or word.startswith(("/", "\\"))
                or re.match(r"[A-Za-z]:", word) or Path(word).is_absolute()):
            return False
    program = Path(words[0]).name.lower()
    if program == "git":
        subcommand = next((word for word in words[1:] if not word.startswith("-")), None)
        return subcommand in _READ_ONLY_GIT and not any(
            word in ("--output", "-o") or word.startswith("--output=") for word in words[1:])
    return program in _READ_ONLY


def _reason(probability: float) -> str:
    return ("Jev advisory guard flagged this command as potentially destructive or sensitive "
            "(p=%.2f). Confirm it is intended before it runs." % probability)


def _format(harness: str, reason: str) -> Dict[str, Any]:
    if harness == "cursor":
        return {"permission": "ask", "user_message": reason, "agent_message": reason}
    if harness == "claude-code":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                       "permissionDecisionReason": reason}}
    blocked = reason + " It was not run. Ask the user to confirm and run it themselves if intended."
    if harness == "gemini-cli":
        return {"decision": "deny", "reason": blocked, "systemMessage": reason}
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": blocked}}


def evaluate_hook(harness: str, payload: Any, *, client: Any = None,
                  threshold: float = DEFAULT_THRESHOLD, when: str = "unattended") -> Optional[Dict[str, Any]]:
    """Return the harness-formatted escalation, or ``None`` to leave the harness unchanged."""
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0 < threshold <= 1:
        raise ValueError("invalid_hook_threshold")
    if when not in ("unattended", "always"):
        raise ValueError("invalid_hook_mode")
    command, cwd = extract_command(harness, payload)
    if harness not in ASK_CAPABLE and when == "unattended":
        mode = payload.get("permission_mode") if isinstance(payload, dict) else None
        if not isinstance(mode, str) or mode.replace("_", "").lower() not in UNATTENDED_MODES:
            return None
    if is_plainly_read_only(command):
        return None
    from .client import JevClient
    from .harness_guards import guard_bash_command

    result = guard_bash_command(command, cwd=cwd, client=client or JevClient(timeout_s=HOOK_TIMEOUT_S))
    if result.get("status") != "ok":
        return None
    probabilities = result.get("category_probabilities") or {}
    signals = [value for value in (probabilities.get("destructive_or_sensitive"), result.get("risk_probability"))
               if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not signals or max(signals) < threshold:
        return None
    return _format(harness, _reason(max(signals)))


def run_hook(harness: str, raw: bytes, *, client: Any = None, threshold: Optional[float] = None,
             when: str = "unattended", environ: Optional[Mapping[str, str]] = None) -> str:
    """Process one hook invocation; always returns text for stdout (possibly empty)."""
    environ = os.environ if environ is None else environ
    if environ.get("JEV_HOOK", "").strip().lower() in ("0", "off", "false", "disabled"):
        return ""
    try:
        if threshold is None:
            threshold = float(environ.get("JEV_HOOK_THRESHOLD") or DEFAULT_THRESHOLD)
        if len(raw) > MAX_HOOK_INPUT_BYTES:
            return ""
        from .client import _decode

        decision = evaluate_hook(harness, _decode(raw), client=client, threshold=threshold, when=when)
    except Exception:
        # Fail open: the harness's own permission flow is unchanged.
        return ""
    return json.dumps(decision, ensure_ascii=False) if decision else ""


def _quote(value: str) -> str:
    if os.name == "nt":
        if '"' in value:
            raise ValueError("unsupported_hook_path")
        return '"' + value + '"'
    return shlex.quote(value)


def hook_config(harness: str, *, runtime_home: Path, python: Optional[str] = None,
                when: str = "unattended") -> Dict[str, Any]:
    """Return the settings fragment and target files for one harness hook."""
    if harness not in HOOK_HARNESSES:
        raise ValueError("unknown_hook_harness")
    from .harnesses import launcher_python

    python = python or launcher_python()
    argv = ["-I", "-m", "jev_decision.cli", "--runtime-home", str(runtime_home), "hook", "run", harness]
    if when != "unattended" and harness not in ASK_CAPABLE:
        argv += ["--when", when]
    shell = " ".join([_quote(python)] + [_quote(item) if item == str(runtime_home) else item for item in argv])
    if harness == "claude-code":
        fragment = {"hooks": {"PreToolUse": [{"matcher": "Bash|PowerShell", "hooks": [
            {"type": "command", "command": python, "args": argv, "timeout": 10}]}]}}
        files = ["~/.claude/settings.json", "<project>/.claude/settings.json"]
    elif harness == "command-code":
        fragment = {"hooks": {"PreToolUse": [{"matcher": "^shell$", "hooks": [
            {"type": "command", "command": shell, "timeout": 10}]}]}}
        files = ["~/.commandcode/settings.json", "<project>/.commandcode/settings.json"]
    elif harness == "codex":
        fragment = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": shell, "timeout": 10, "statusMessage": "Jev guard"}]}]}}
        files = ["~/.codex/hooks.json", "<project>/.codex/hooks.json"]
    elif harness == "cursor":
        fragment = {"version": 1, "hooks": {"beforeShellExecution": [{"command": shell, "timeout": 10}]}}
        files = ["~/.cursor/hooks.json", "<project>/.cursor/hooks.json"]
    else:
        fragment = {"hooks": {"BeforeTool": [{"matcher": "run_shell_command", "hooks": [
            {"name": "jev-guard", "type": "command", "command": shell, "timeout": 10000}]}]}}
        files = ["~/.gemini/settings.json", "<project>/.gemini/settings.json"]
    return {"harness": harness, "merge_into": files, "fragment": fragment,
            "decision": "ask" if harness in ASK_CAPABLE else "deny",
            "acts": "always" if harness in ASK_CAPABLE or when == "always" else "unattended sessions only",
            "note": "Merge the fragment into existing hooks; do not replace other entries. Reload the client. "
                    "Set JEV_HOOK=off to disable temporarily. The hook never approves a command."}
