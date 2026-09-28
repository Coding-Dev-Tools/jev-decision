"""Reversible, content-free installation into detected local harness profiles.

Only our named MCP entry and skill are managed. Model/provider settings, hooks,
permissions and other servers are never rewritten. Preview and status are reads.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .runtime import RuntimeConfig

SERVER = "jev"
SKILL = "jev-advice"
_MISSING = object()
_LIMIT = 4 * 1024 * 1024
_BEGIN = "# >>> jev-decision managed MCP"
_END = "# <<< jev-decision managed MCP"
HARNESS_TARGETS = frozenset({"codex", "command-code", "antigravity", "antigravity-ide",
    "claude-code", "claude-desktop", "cursor", "opencode", "crush", "pi", "hermes",
    "omp", "openclaude", "copilot", "gemini-cli"})
PROJECT_TARGETS = frozenset({"codex", "command-code", "claude-code", "cursor", "gemini-cli",
                           "antigravity", "antigravity-ide", "opencode"})


class HarnessError(ValueError):
    """Messages are fixed error codes, never configuration contents."""


@dataclass
class _Member:
    key: str
    start: int
    node: Any
    comma: Optional[int] = None


@dataclass
class _Node:
    value: Any
    start: int
    end: int
    members: List[_Member] = field(default_factory=list)


class _JSON:
    """Small span parser; keeps comments and whitespace outside the owned entry."""

    def __init__(self, text: str, comments: bool):
        self.text, self.comments, self.pos = text, comments, 0

    def space(self):
        while self.pos < len(self.text):
            if self.text[self.pos].isspace():
                self.pos += 1
            elif self.comments and self.text.startswith("//", self.pos):
                end = self.text.find("\n", self.pos)
                self.pos = len(self.text) if end < 0 else end + 1
            elif self.comments and self.text.startswith("/*", self.pos):
                end = self.text.find("*/", self.pos + 2)
                if end < 0:
                    raise HarnessError("invalid_configuration")
                self.pos = end + 2
            else:
                break

    def value(self):
        self.space()
        start = self.pos
        if self.pos >= len(self.text):
            raise HarnessError("invalid_configuration")
        char = self.text[self.pos]
        if char not in "{[":
            try:
                value, self.pos = json.JSONDecoder().raw_decode(self.text, self.pos)
            except (ValueError, RecursionError):
                raise HarnessError("invalid_configuration") from None
            if isinstance(value, float) and not __import__("math").isfinite(value):
                raise HarnessError("invalid_configuration")
            return _Node(value, start, self.pos)
        self.pos += 1
        closing, value, members = ("}", {}, []) if char == "{" else ("]", [], [])
        self.space()
        while self.pos < len(self.text) and self.text[self.pos] != closing:
            self.space()
            key_start = self.pos
            if char == "{":
                key = self.value().value
                self.space()
                if not isinstance(key, str) or key in value or self.text[self.pos:self.pos + 1] != ":":
                    raise HarnessError("invalid_configuration")
                self.pos += 1
            node = self.value()
            if char == "{":
                value[key] = node.value
                members.append(_Member(key, key_start, node))
            else:
                value.append(node.value)
            self.space()
            if self.text[self.pos:self.pos + 1] != ",":
                break
            if members:
                members[-1].comma = self.pos
            self.pos += 1
            self.space()
            if self.text[self.pos:self.pos + 1] == closing and not self.comments:
                raise HarnessError("invalid_configuration")
        if self.text[self.pos:self.pos + 1] != closing:
            raise HarnessError("invalid_configuration")
        self.pos += 1
        return _Node(value, start, self.pos, members)

    def document(self):
        try:
            node = self.value()
            self.space()
            if self.pos != len(self.text) or not isinstance(node.value, dict):
                raise HarnessError("invalid_configuration")
            return node
        except (IndexError, RecursionError):
            raise HarnessError("invalid_configuration") from None


def _member(node, key):
    return next((item for item in node.members if item.key == key), None)


def _json_edit(text, node, key, value):
    existing = _member(node, key)
    if existing and value is not _MISSING:
        indent = " " * (existing.start - text.rfind("\n", 0, existing.start) - 1)
        rendered = json.dumps(value, indent=2, ensure_ascii=False).replace("\n", "\n" + indent)
        return text[:existing.node.start] + rendered + text[existing.node.end:]
    if existing:
        edits = [(existing.start, existing.node.end, "")]
        if existing.comma is not None:
            edits.append((existing.comma, existing.comma + 1, ""))
        else:
            index = node.members.index(existing)
            if index and node.members[index - 1].comma is not None:
                comma = node.members[index - 1].comma
                edits.append((comma, comma + 1, ""))
        for start, end, replacement in sorted(edits, reverse=True):
            text = text[:start] + replacement + text[end:]
        return text
    if value is _MISSING:
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    close = node.end - 1
    line_start = text.rfind("\n", 0, close) + 1
    insertion = line_start if not text[line_start:close].strip() else close
    parent_indent = re.match(r"[ \t]*", text[text.rfind("\n", 0, node.start) + 1:]).group()
    indent = parent_indent + "  "
    rendered = json.dumps(value, indent=2, ensure_ascii=False).replace("\n", newline + indent)
    fragment = indent + json.dumps(key) + ": " + rendered + newline
    if insertion and not text[:insertion].endswith("\n"):
        fragment = newline + fragment
    if insertion == close:
        fragment += parent_indent
    text = text[:insertion] + fragment + text[insertion:]
    if node.members and node.members[-1].comma is None:
        position = node.members[-1].node.end
        text = text[:position] + "," + text[position:]
    return text


def _toml(text):
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            raise HarnessError("toml_parser_unavailable") from None
    try:
        result = tomllib.loads(text)
        if not isinstance(result.get("mcp_servers", {}), dict):
            raise HarnessError("configuration_schema_conflict")
        return result
    except ValueError:
        raise HarnessError("invalid_configuration") from None


def _toml_block(python, key_env=None, runtime_home=None):
    return (_BEGIN + "\n[mcp_servers.jev]\ncommand = " + json.dumps(python) +
            '\nargs = ["-I", "-m", "jev_decision.mcp"]\nenabled = true\n' +
            ('env = { JEV_HOME = ' + json.dumps(str(runtime_home)) + ' }\n' if runtime_home else "") +
            ("env_vars = " + json.dumps([key_env]) + "\n" if key_env else "") + _END + "\n")


@dataclass
class _Artifact:
    path: Path
    kind: str
    value: Any
    parent: Optional[str] = None
    clients: List[str] = field(default_factory=list)
    detected: bool = True
    scope: str = "user"
    project_root: Optional[str] = None

    @property
    def identity(self):
        value = str(self.path.absolute()) + "\0" + self.kind + "\0" + str(self.parent)
        return hashlib.sha256(value.encode()).hexdigest()[:24]


def _read(path):
    if path.is_symlink():
        raise HarnessError("symlink_target_refused")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > _LIMIT:
        raise HarnessError("configuration_size_or_type_rejected")
    return path.read_bytes()


def _decode(raw):
    try:
        return raw.decode("utf-8-sig") if raw is not None else ""
    except UnicodeError:
        raise HarnessError("configuration_encoding_unsupported") from None


def _current(artifact, raw):
    if raw is None:
        return _MISSING
    text = _decode(raw)
    if artifact.kind in {"skill", "launcher"}:
        return text
    if artifact.kind == "toml":
        document = _toml(text)
        value = document.get("mcp_servers", {}).get(SERVER, _MISSING)
        if value is _MISSING:
            if _BEGIN in text or _END in text:
                raise HarnessError("managed_marker_conflict")
            return value
        # Exact owned source also protects comments a user adds inside the block.
        if text.count(_BEGIN) != 1 or text.count(_END) != 1:
            return value
        start, end = text.index(_BEGIN), text.index(_END) + len(_END)
        if end <= start:
            raise HarnessError("managed_marker_conflict")
        if text[end:end + 2] == "\r\n":
            end += 2
        elif text[end:end + 1] == "\n":
            end += 1
        block = text[start:end]
        if _toml(block).get("mcp_servers", {}).get(SERVER) != value:
            raise HarnessError("managed_marker_conflict")
        return block
    root = _JSON(text, artifact.kind == "jsonc").document()
    parent = root.value.get(artifact.parent, {})
    if not isinstance(parent, dict):
        raise HarnessError("configuration_schema_conflict")
    return parent.get(SERVER, _MISSING)


def _render(artifact, raw, value, remove_parent=False):
    text = _decode(raw)
    if artifact.kind in {"skill", "launcher"}:
        return None if value is _MISSING else value.encode("utf-8")
    if artifact.kind == "toml":
        current = _current(artifact, raw)
        if current is _MISSING:
            result = text + ("\n" if text and not text.endswith("\n\n") else "") + value
        else:
            if not isinstance(current, str) or not current.startswith(_BEGIN):
                raise HarnessError("managed_marker_conflict")
            result = text.replace(current, "" if value is _MISSING else value, 1)
        _toml(result)
    else:
        text = text or "{}\n"
        root = _JSON(text, artifact.kind == "jsonc").document()
        parent = _member(root, artifact.parent)
        if parent is None:
            result = text if value is _MISSING else _json_edit(text, root, artifact.parent, {SERVER: value})
        else:
            if not isinstance(parent.node.value, dict):
                raise HarnessError("configuration_schema_conflict")
            result = _json_edit(text, parent.node, SERVER, value)
            if value is _MISSING and remove_parent:
                changed = _JSON(result, artifact.kind == "jsonc").document()
                if changed.value.get(artifact.parent) == {}:
                    result = _json_edit(result, changed, artifact.parent, _MISSING)
        _JSON(result, artifact.kind == "jsonc").document()
    encoded = result.encode("utf-8")
    return (b"\xef\xbb\xbf" + encoded) if raw and raw.startswith(b"\xef\xbb\xbf") else encoded


def _location(name, default, filename=None):
    value = os.environ.get(name)
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        raise HarnessError("relative_harness_location_rejected")
    if filename and path.suffix.lower() not in (".json", ".jsonc", ".toml"):
        path = path / filename
    return path


def _skill(python, inactive=False, runtime_home=None, template_name="jev-skill.md"):
    template = (Path(__file__).parent / "resources" / template_name).read_text(encoding="utf-8")
    command = ("& '" + python.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(python))
    command += " -I -m jev_decision.cli"
    if runtime_home is not None:
        path = str(runtime_home)
        command += " --runtime-home " + ("'" + path.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(path))
    return (template.replace("{{CLI_COMMAND}}", command)
            .replace("{{SHELL}}", "powershell" if os.name == "nt" else "sh")
            .replace("{{ACTIVATION}}", "This profile has no verified runnable client. These are inactive setup instructions; no operational integration is claimed.\n" if inactive else ""))


def _discover(target=None, scope="user", project_root=None, runtime=None):
    if target is not None and (not isinstance(target, str) or target not in HARNESS_TARGETS):
        raise HarnessError("unknown_harness_target")
    if not isinstance(scope, str) or scope not in {"user", "project"}:
        raise HarnessError("invalid_harness_scope")
    if scope == "project":
        if target not in PROJECT_TARGETS:
            raise HarnessError("project_scope_unsupported_for_target")
        if not isinstance(project_root, (str, Path)) or not Path(project_root).expanduser().is_absolute():
            raise HarnessError("absolute_project_root_required")
        project_root = Path(project_root).expanduser().resolve()
        if not project_root.is_dir():
            raise HarnessError("project_root_not_found")
    elif project_root is not None:
        raise HarnessError("project_root_requires_project_scope")
    runtime = runtime or RuntimeConfig.load()
    home = Path.home()
    local = _location("LOCALAPPDATA", home / "AppData" / "Local")
    xdg = _location("XDG_CONFIG_HOME", home / ".config")
    codex = _location("CODEX_HOME", home / ".codex")
    python = str(Path(sys.executable).resolve())
    stdio = {"command": python, "args": ["-I", "-m", "jev_decision.mcp"],
             "env": {"JEV_HOME": str(runtime.home)}}
    artifacts, clients = {}, []

    def add(name, profile, commands, config=None, kind="json", parent="mcpServers", value=None,
            skill_root=None, executable=None, inactive_if_missing=False, skill_template="jev-skill.md"):
        if target is not None and name != target:
            return
        if scope == "project":
            mappings = {
                "codex": (".codex/config.toml", ".agents/skills"),
                "command-code": (".mcp.json", ".commandcode/skills"),
                "claude-code": (".mcp.json", ".claude/skills"),
                "cursor": (".cursor/mcp.json", ".cursor/skills"),
                "gemini-cli": (".gemini/settings.json", ".gemini/skills"),
                "antigravity": (".agents/mcp_config.json", ".agents/skills"),
                "antigravity-ide": (".agents/mcp_config.json", ".agents/skills"),
                "opencode": ("opencode.json" if (project_root / "opencode.json").exists() else "opencode.jsonc", ".opencode/skills"),
            }
            config_path, skills_path = mappings[name]
            config, skill_root = project_root / config_path, project_root / skills_path
            profile = project_root
        runnable = any(shutil.which(command) is not None for command in commands)
        runnable = runnable or bool(executable and executable.is_file())
        detected = runnable or profile.exists() or bool(config and config.exists())
        inactive = inactive_if_missing and not runnable
        clients.append({"name": name, "detected": detected, "runnable_detected": runnable,
                        "adapter": "inactive_guidance" if inactive else "mcp_and_skill" if config else "cli_skill",
                        "configured": False, "launcher_executable_present": Path(python).is_file(),
                        "launchable": None, "mcp_connected": False, "provider_authenticated": False,
                        "actual_client_verified": False, "operational_verified": False,
                        "scope": scope, "selected": name == target})
        entries = []
        if config:
            entries.append(_Artifact(config, kind, value, parent, [name], detected or name == target,
                                     scope, str(project_root) if project_root else None))
        if skill_root:
            entries.append(_Artifact(skill_root / SKILL / "SKILL.md", "skill", _skill(python, inactive, runtime.home, skill_template), None,
                                     [name], detected or name == target, scope,
                                     str(project_root) if project_root else None))
        for item in entries:
            previous = artifacts.get(item.identity)
            if previous:
                previous.clients.extend(item.clients)
                previous.detected = previous.detected or item.detected
            else:
                artifacts[item.identity] = item

    add("codex", codex, ["codex"], codex / "config.toml", "toml", "mcp_servers",
        _toml_block(python, runtime.key_env if runtime.credential_source == "env" else None, runtime.home), codex / "skills")
    root = home / ".commandcode"
    command_stdio = dict(stdio, transport="stdio", enabled=True, env=dict(stdio["env"]))
    if runtime.credential_source == "env":
        # Empty fallback keeps off-mode evidence reads available without a key.
        command_stdio["env"][runtime.key_env] = "${" + runtime.key_env + ":-}"
    add("command-code", root, ["cmdc", "commandcode"], root / "mcp.json", value=command_stdio,
        skill_root=root / "skills", executable=local / "Programs" / "Command Code" / "Command Code.exe",
        skill_template="command-code-skill.md")
    gemini = home / ".gemini"
    for name, folder, exe in (("antigravity", "antigravity", "Antigravity.exe"),
                              ("antigravity-ide", "Antigravity IDE", "Antigravity IDE.exe")):
        add(name, gemini / name, [name], gemini / "config" / "mcp_config.json", value=stdio,
            skill_root=gemini / "config" / "skills", executable=local / "Programs" / folder / exe)
    claude_stdio = dict(stdio, type="stdio", env=dict(stdio["env"]))
    if runtime.credential_source == "env":
        claude_stdio["env"][runtime.key_env] = "${" + runtime.key_env + "}"
    add("claude-code", home / ".claude", ["claude"], home / ".claude.json", value=claude_stdio,
        skill_root=home / ".claude" / "skills")
    if sys.platform == "win32":
        desktop = _location("APPDATA", home / "AppData" / "Roaming") / "Claude"
        desktop_exe = local / "Programs" / "Claude" / "Claude.exe"
    elif sys.platform == "darwin":
        desktop = home / "Library" / "Application Support" / "Claude"
        desktop_exe = Path("/Applications/Claude.app/Contents/MacOS/Claude")
    else:
        desktop = desktop_exe = None
    if desktop is not None:
        add("claude-desktop", desktop, ["claude-desktop"], desktop / "claude_desktop_config.json",
            value=stdio, executable=desktop_exe)
    elif target == "claude-desktop":
        raise HarnessError("claude_desktop_platform_unsupported")
    cursor_stdio = dict(stdio, env=dict(stdio["env"]))
    if runtime.credential_source == "env":
        cursor_stdio["env"][runtime.key_env] = "${env:" + runtime.key_env + "}"
    add("cursor", home / ".cursor", ["cursor"], home / ".cursor" / "mcp.json", value=cursor_stdio,
        skill_root=home / ".cursor" / "skills", executable=local / "Programs" / "cursor" / "Cursor.exe")
    root = xdg / "opencode"
    oc = _location("OPENCODE_CONFIG", root / "opencode.jsonc")
    if not os.environ.get("OPENCODE_CONFIG") and (root / "opencode.json").exists():
        oc = root / "opencode.json"
    add("opencode", root, ["opencode"], oc, "jsonc", "mcp",
        {"type": "local", "command": [python, "-I", "-m", "jev_decision.mcp"], "enabled": True,
         "environment": {"JEV_HOME": str(runtime.home)}}, root / "skills")
    crush_global = _location("CRUSH_GLOBAL_CONFIG", xdg / "crush" / "crush.json", "crush.json")
    crush_data = _location("CRUSH_GLOBAL_DATA", local / "crush", "crush.json")
    crush = crush_global if crush_global.exists() or os.environ.get("CRUSH_GLOBAL_CONFIG") else crush_data
    add("crush", local / "crush", ["crush"], crush, parent="mcp", value=dict(stdio, type="stdio"),
        skill_root=local / "crush" / "skills")
    gemini_stdio = dict(stdio, env=dict(stdio["env"]))
    if runtime.credential_source == "env":
        # Gemini strips sensitive inherited variables unless the server names
        # them explicitly. Resolve the reference in the client, never here.
        gemini_stdio["env"][runtime.key_env] = "${" + runtime.key_env + "}"
    add("gemini-cli", gemini, ["gemini"], gemini / "settings.json", value=gemini_stdio,
        skill_root=gemini / "skills", inactive_if_missing=True)
    for name, profile, commands in (("pi", home / ".pi" / "agent", ["pi"]),
                                    ("hermes", home / ".hermes", ["hermes"]),
                                    ("omp", home / ".omp" / "agent", ["omp"]),
                                    ("openclaude", home / ".openclaude", ["openclaude"]),
                                    ("copilot", home / ".copilot", ["copilot"])):
        add(name, profile, commands, skill_root=profile / "skills", inactive_if_missing=name in {"copilot", "gemini-cli"})
    # Redirect legacy PATH commands without changing global Python packages or
    # PATH. Only use an existing user bin directory already on PATH.
    user_bin = home / "bin"
    path_dirs = [os.path.normcase(str(Path(value).resolve())) for value in os.environ.get("PATH", "").split(os.pathsep) if value]
    if target is None and scope == "user" and os.name == "nt" and user_bin.is_dir() and os.path.normcase(str(user_bin.resolve())) in path_dirs:
        if any(char in python + str(runtime.home) for char in ('"', '%', '\r', '\n')):
            raise HarnessError("launcher_path_unsupported")
        for filename, module in (("jev.cmd", "jev_decision.cli"), ("jev-mcp.cmd", "jev_decision.mcp")):
            value = '@echo off\r\nsetlocal\r\nset "JEV_HOME=' + str(runtime.home) + '"\r\n"' + python + '" -I -m ' + module + ' %*\r\n'
            item = _Artifact(user_bin / filename, "launcher", value, None, ["jev-cli"])
            artifacts[item.identity] = item
    return artifacts, clients


def _digest(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else None


def _atomic(path, raw, private=False):
    from .credentials import _restrict_acl
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=str(path.parent), prefix=".jev-", delete=False) as stream:
            temporary = Path(stream.name)
            if private:
                _restrict_acl(temporary, directory=False)
            elif path.exists() and os.name != "nt":
                os.chmod(temporary, path.stat().st_mode & 0o777)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


@contextlib.contextmanager
def _lock(directory):
    from .credentials import _restrict_acl
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _restrict_acl(directory, directory=True)
    with open(directory / "harness-install.lock", "a+b") as stream:
        _restrict_acl(Path(stream.name), directory=False)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise HarnessError("installer_busy") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _manifest(path):
    raw = _read(path)
    if raw is None:
        return {"version": 1, "entries": {}}
    try:
        value = json.loads(_decode(raw))
    except ValueError:
        raise HarnessError("invalid_ownership_manifest") from None
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("entries"), dict):
        raise HarnessError("invalid_ownership_manifest")
    return value


def _save_manifest(path, manifest):
    _atomic(path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(), private=True)


def _owned(record, current):
    if current is _MISSING:
        return None
    for key in ("pending", "managed"):
        candidate = record.get(key)
        if isinstance(candidate, dict) and candidate.get("value", _MISSING) == current:
            return candidate
    return None


def _install_one(artifact, record, manifest, manifest_path, directory, apply):
    raw = _read(artifact.path)
    current = _current(artifact, raw)
    owned = _owned(record, current) if record else None
    if record and current is not _MISSING and not owned:
        return "modified_conflict"
    if not record and current is not _MISSING:
        return "unmanaged_conflict"
    if owned and current == artifact.value:
        return "configured"
    rendered = _render(artifact, raw, artifact.value)
    if not apply:
        return "would_update" if record else "would_install"
    if _read(artifact.path) != raw:
        raise HarnessError("configuration_changed_during_install")
    if record is None:
        backup = artifact.identity + ".original"
        if raw is not None:
            _atomic(directory / backup, raw, private=True)
        parent_created = False
        if artifact.kind in {"json", "jsonc"}:
            parent_created = raw is None or artifact.parent not in _JSON(_decode(raw), artifact.kind == "jsonc").document().value
        record = {"path": str(artifact.path.absolute()), "kind": artifact.kind, "parent": artifact.parent,
                  "before_exists": raw is not None, "backup": backup if raw is not None else None,
                  "parent_created": parent_created, "whole_file_owned": True,
                  "scope": artifact.scope, "project_root": artifact.project_root, "clients": artifact.clients}
        manifest["entries"][artifact.identity] = record
    elif owned:
        record["whole_file_owned"] = record.get("whole_file_owned", False) and _digest(raw) == owned.get("digest")
    else:
        # A deleted owned entry may be reinstalled, but unrelated edits are retained.
        record["whole_file_owned"] = False
    record["pending"] = {"value": artifact.value, "digest": _digest(rendered)}
    _save_manifest(manifest_path, manifest)  # recovery data precedes the mutation
    _atomic(artifact.path, rendered)
    record["managed"] = record.pop("pending")
    _save_manifest(manifest_path, manifest)
    return "updated" if owned else "installed"


def _restore_one(artifact, record, manifest, manifest_path, directory, apply):
    if not record:
        return "not_managed"
    raw = _read(artifact.path)
    current = _current(artifact, raw)
    owned = _owned(record, current)
    if current is not _MISSING and not owned:
        return "modified_conflict"
    if current is _MISSING:
        if apply:
            del manifest["entries"][artifact.identity]
            _save_manifest(manifest_path, manifest)
        return "already_absent"
    if record.get("whole_file_owned") and _digest(raw) == owned.get("digest"):
        if record.get("before_exists"):
            backup = record.get("backup")
            if backup != artifact.identity + ".original":
                raise HarnessError("invalid_backup_reference")
            result = _read(directory / backup)
            if result is None:
                raise HarnessError("original_backup_missing")
        else:
            result = None
    else:
        result = _render(artifact, raw, _MISSING, record.get("parent_created", False))
    if not apply:
        return "would_restore"
    if _read(artifact.path) != raw:
        raise HarnessError("configuration_changed_during_restore")
    if result is None:
        archive = directory / "restored-files"
        archive.mkdir(exist_ok=True)
        import uuid
        os.replace(str(artifact.path), str(archive / (artifact.identity + "-" + uuid.uuid4().hex)))
    else:
        _atomic(artifact.path, result)
    del manifest["entries"][artifact.identity]
    _save_manifest(manifest_path, manifest)
    return "restored"


def run_harness_command(action, apply=False, *, target=None, scope="user", project_root=None,
                        config=None) -> Dict[str, Any]:
    """Preview by default; report metadata only, including for malformed configs."""
    if not isinstance(action, str) or action not in {"preview", "install", "status", "restore"} or type(apply) is not bool:
        raise HarnessError("invalid_harness_action")
    apply = apply and action in {"install", "restore"}
    config = config or RuntimeConfig.load()
    directory = config.home / "harness-backups"
    manifest_path = directory / "ownership.json"
    artifacts, clients = _discover(target, scope, project_root, runtime=config)
    result = {"action": "preview" if action == "install" and not apply else action,
              "applied": apply, "status": "ok", "runtime_home": str(config.home),
              "target": target, "scope": scope,
              "harnesses": clients, "items": [], "operational_verified": False,
              "limitations": ["Running clients need reload or restart and an actual-client smoke test.",
                              "Project settings may override user integrations.",
                              "Web clients require a separately configured remote connector."]}
    with _lock(directory) if apply else contextlib.nullcontext():
        manifest = _manifest(manifest_path)
        for identity, artifact in artifacts.items():
            record = manifest["entries"].get(identity)
            if not artifact.detected and not record:
                continue
            item = {"clients": artifact.clients, "path": str(artifact.path.absolute()), "kind": artifact.kind}
            try:
                if record and (not isinstance(record, dict) or record.get("path") != str(artifact.path.absolute()) or
                               record.get("kind") != artifact.kind or record.get("parent") != artifact.parent):
                    raise HarnessError("invalid_ownership_record")
                if record and artifact.scope == "project" and artifact.path.name == ".mcp.json":
                    # Command Code and Claude Code consume this same project
                    # entry. A selected client must not take over or restore
                    # another client's managed entry merely because paths match.
                    previous_clients = record.get("clients", [])
                    if not isinstance(previous_clients, list) or any(not isinstance(name, str) for name in previous_clients):
                        raise HarnessError("invalid_ownership_record")
                    involved = set(previous_clients) | set(artifact.clients)
                    if "command-code" in involved and not set(previous_clients).intersection(artifact.clients):
                        raise HarnessError("shared_client_ownership_conflict")
                if action == "restore":
                    status = _restore_one(artifact, record, manifest, manifest_path, directory, apply)
                elif action == "status":
                    current = _current(artifact, _read(artifact.path))
                    owned = _owned(record, current) if record else None
                    status = ("configured" if owned and current == artifact.value else "update_available" if owned else
                              "modified_conflict" if record and current is not _MISSING else
                              "unmanaged_conflict" if current is not _MISSING else "not_configured")
                else:
                    status = _install_one(artifact, record, manifest, manifest_path, directory, apply)
                item["status"] = status
            except (OSError, UnicodeError, ValueError) as error:
                item["status"] = "error"
                item["error_code"] = str(error) if isinstance(error, HarnessError) else "local_configuration_error"
            if item["status"] in {"error", "modified_conflict", "unmanaged_conflict"}:
                result["status"] = "partial"
            result["items"].append(item)
        for client in clients:
            rows = [row for row in result["items"] if client["name"] in row["clients"]]
            client["configured"] = bool(rows) and all(row["status"] in {"configured", "installed", "updated"} for row in rows)
        unknown = set(manifest["entries"]) - set(artifacts)
        if target is not None:
            result["unselected_managed_targets"] = len(unknown)
            unknown = set()
        else:
            outside_scope = {identity for identity in unknown
                             if isinstance(manifest["entries"][identity], dict)
                             and manifest["entries"][identity].get("scope") == "project"}
            result["unselected_managed_targets"] = len(outside_scope)
            unknown -= outside_scope
        if unknown:
            result["status"] = "partial"
            result["unrecognized_managed_targets"] = len(unknown)
    return result
