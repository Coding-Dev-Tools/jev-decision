"""Installer tests use isolated profiles, synthetic contents and no client launches."""
import json
import os
import sys
from pathlib import Path

import pytest

from jev_decision import credentials, harnesses
from jev_decision.runtime import RuntimeConfig


@pytest.mark.skipif(os.name != "nt", reason="Windows PATH launcher integration")
def test_legacy_cli_launcher_is_isolated_and_reversible(profiles, monkeypatch):
    home, _, _ = profiles
    user_bin = home / "bin"
    user_bin.mkdir()
    monkeypatch.setenv("PATH", str(user_bin))
    installed = harnesses.run_harness_command("install", apply=True)
    assert installed["status"] == "ok"
    shim = user_bin / "jev.cmd"
    assert '" -I -m jev_decision.cli %*' in shim.read_text()
    assert harnesses.run_harness_command("install", apply=True)["status"] == "ok"
    restored = harnesses.run_harness_command("restore", apply=True)
    assert restored["status"] == "ok" and not shim.exists()
    assert list((RuntimeConfig.load().home / "harness-backups" / "restored-files").iterdir())


def test_cli_reports_partial_install_as_failure(monkeypatch, capsys):
    from jev_decision.cli import main
    monkeypatch.setattr(harnesses, "run_harness_command", lambda *a, **kw: {"status": "partial"})
    assert main(["harness", "install"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "partial"


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    home = tmp_path / "fake home"
    home.mkdir()
    local = home / "AppData" / "Local"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for name in ("OPENCODE_CONFIG", "CRUSH_GLOBAL_CONFIG", "CRUSH_GLOBAL_DATA"):
        monkeypatch.delenv(name, raising=False)
    protected = []
    monkeypatch.setattr(credentials, "_restrict_acl", lambda path, directory: protected.append((Path(path), directory)))
    for relative in (".codex", ".commandcode", ".gemini/antigravity", ".gemini/antigravity-ide",
                     ".claude", ".cursor", ".config/opencode", ".pi/agent", ".hermes", ".omp/agent",
                     ".openclaude", ".copilot", "AppData/Local/crush"):
        (home / relative).mkdir(parents=True, exist_ok=True)
    files = {
        ".codex/config.toml": '# retain comment\nmodel = "normal-model"\n[mcp_servers.engraphis]\ncommand = "original-launcher"\n',
        ".commandcode/mcp.json": '{"mcpServers":{"engraphis":{"transport":"stdio","enabled":true}},"private":"synthetic-secret"}\n',
        ".commandcode/settings.json": '{"hooks":{"Stop":[]},"permissions":{"allow":["existing"]}}\n',
        ".gemini/config/mcp_config.json": '{"mcpServers":{"engraphis":{"command":"original-launcher"}}}\n',
        ".claude.json": '{"oauthAccount":{"token":"synthetic-secret"},"mcpServers":{"engraphis":{"type":"http"}}}\n',
        ".cursor/mcp.json": '{"mcpServers":{"engraphis":{"url":"http://127.0.0.1:8711"}}}\n',
        ".config/opencode/opencode.jsonc": '{\n  // preserve provider commentary\n  "model": "normal-model",\n  "mcp": {\n    "engraphis": {"type":"remote","enabled":true}, // keep inline note\n  },\n}\n',
        "AppData/Local/crush/crush.json": '{"providers":{"normal":{"token":"synthetic-secret"}},"mcp":{"engraphis":{"type":"http"}}}\n',
    }
    for relative, text in files.items():
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    return home, files, protected


def _rows(result, kind=None):
    return [row for row in result["items"] if kind is None or row["kind"] == kind]


def _json(path, comments=False):
    return harnesses._JSON(path.read_text(encoding="utf-8-sig"), comments).document().value


def test_preview_and_status_do_not_write_or_expose_configuration(profiles):
    home, originals, _ = profiles
    before = {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    result = harnesses.run_harness_command("install")
    assert result["action"] == "preview" and result["applied"] is False
    assert all(row["status"] == "would_install" for row in result["items"])
    assert "synthetic-secret" not in json.dumps(result)
    assert not RuntimeConfig.load().home.exists()
    status = harnesses.run_harness_command("status", apply=True)
    assert status["applied"] is False
    assert all(row["status"] == "not_configured" for row in status["items"])
    assert before == {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    assert all(not client["operational_verified"] for client in status["harnesses"])


def test_native_schemas_and_skill_fallbacks_preserve_existing_settings(profiles):
    home, originals, protected = profiles
    result = harnesses.run_harness_command("install", apply=True)
    assert result["status"] == "ok"
    assert all(row["status"] == "installed" for row in result["items"])
    python = str(Path(sys.executable).resolve())
    command = _json(home / ".commandcode/mcp.json")["mcpServers"]["jev"]
    assert command == {"transport": "stdio", "enabled": True, "command": python, "args": ["-I", "-m", "jev_decision.mcp"]}
    assert _json(home / ".claude.json")["mcpServers"]["jev"]["type"] == "stdio"
    assert _json(home / ".gemini/config/mcp_config.json")["mcpServers"]["jev"] == {"command": python, "args": ["-I", "-m", "jev_decision.mcp"]}
    oc = home / ".config/opencode/opencode.jsonc"
    assert _json(oc, True)["mcp"]["jev"] == {"type": "local", "command": [python, "-I", "-m", "jev_decision.mcp"], "enabled": True}
    assert '// preserve provider commentary' in oc.read_text()
    assert '// keep inline note' in oc.read_text()
    assert _json(home / "AppData/Local/crush/crush.json")["mcp"]["jev"]["type"] == "stdio"
    codex = (home / ".codex/config.toml").read_text()
    assert codex.startswith(originals[".codex/config.toml"])
    assert harnesses._toml(codex)["mcp_servers"]["jev"]["command"] == python
    assert (home / ".commandcode/settings.json").read_text() == originals[".commandcode/settings.json"]
    for directory in (".pi/agent", ".hermes", ".omp/agent", ".openclaude"):
        skill = (home / directory / "skills/jev-advice/SKILL.md").read_text()
        assert python in skill and " -I -m jev_decision.cli" in skill and "{{" not in skill
        assert not (home / directory / "mcp.json").exists()
    assert (home / ".gemini/config/skills/jev-advice/SKILL.md").is_file()
    assert (home / ".copilot/skills/jev-advice/SKILL.md").read_text().count("inactive setup instructions") == 1
    assert "synthetic-secret" not in json.dumps(result)
    manifest = RuntimeConfig.load().home / "harness-backups/ownership.json"
    assert "synthetic-secret" not in manifest.read_text()
    assert (manifest.parent, True) in protected
    for path in manifest.parent.glob("*.original"):
        assert path.read_bytes() in [value.encode() for value in originals.values()]
    # Restriction is applied to the temporary file before any backup bytes exist.
    assert any(path.name.startswith(".jev-") and not directory for path, directory in protected)


def test_repeat_install_is_idempotent_and_restores_exact_original_bytes(profiles):
    home, originals, _ = profiles
    original = home / ".cursor/mcp.json"
    original.write_bytes(b"\xef\xbb\xbf" + originals[".cursor/mcp.json"].replace("\n", "\r\n").encode())
    before = {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    first = harnesses.run_harness_command("install", apply=True)
    managed = {row["path"]: Path(row["path"]).read_bytes() for row in first["items"]}
    manifest = RuntimeConfig.load().home / "harness-backups/ownership.json"
    ownership = manifest.read_bytes()
    second = harnesses.run_harness_command("install", apply=True)
    assert all(row["status"] == "configured" for row in second["items"])
    assert ownership == manifest.read_bytes()
    assert managed == {path: Path(path).read_bytes() for path in managed}
    preview = harnesses.run_harness_command("restore")
    assert all(row["status"] == "would_restore" for row in preview["items"])
    restored = harnesses.run_harness_command("restore", apply=True)
    assert all(row["status"] == "restored" for row in restored["items"])
    assert before == {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    assert json.loads(manifest.read_text())["entries"] == {}


def test_unrelated_changes_survive_update_then_restore(profiles, monkeypatch):
    home, _, _ = profiles
    harnesses.run_harness_command("install", apply=True)
    path = home / ".config/opencode/opencode.jsonc"
    changed = path.read_text().replace('"normal-model"', '"user-new-model"')
    path.write_text(changed)
    monkeypatch.setattr(harnesses.sys, "executable", str(home / "new runtime/python.exe"))
    updated = harnesses.run_harness_command("install", apply=True)
    assert all(row["status"] == "updated" for row in updated["items"])
    result = harnesses.run_harness_command("restore", apply=True)
    assert result["status"] == "ok"
    assert _json(path, True)["model"] == "user-new-model"
    assert "jev" not in _json(path, True)["mcp"]
    assert "// keep inline note" in path.read_text()
    assert _json(path, True)["mcp"]["engraphis"] == {"type": "remote", "enabled": True}


def test_manual_managed_edits_are_never_overwritten_or_removed(profiles):
    home, _, _ = profiles
    harnesses.run_harness_command("install", apply=True)
    path = home / ".commandcode/mcp.json"
    data = _json(path)
    data["mcpServers"]["jev"]["enabled"] = False
    path.write_text(json.dumps(data))
    skill = home / ".hermes/skills/jev-advice/SKILL.md"
    skill.write_text(skill.read_text() + "\nUser note to retain.\n")
    toml = home / ".codex/config.toml"
    toml.write_text(toml.read_text().replace(harnesses._END, "# User note\n" + harnesses._END))
    snapshots = {str(p): p.read_bytes() for p in (path, skill, toml)}
    for action in ("status", "install", "restore"):
        result = harnesses.run_harness_command(action, apply=True)
        conflicts = {row["path"] for row in result["items"] if row["status"] == "modified_conflict"}
        assert set(snapshots) <= conflicts
        assert all(Path(p).read_bytes() == raw for p, raw in snapshots.items())


def test_unmanaged_existing_jev_is_not_adopted_even_if_value_matches(profiles):
    home, _, _ = profiles
    artifacts, _ = harnesses._discover()
    artifact = next(value for value in artifacts.values() if value.path == home / ".cursor/mcp.json")
    raw = artifact.path.read_bytes()
    artifact.path.write_bytes(harnesses._render(artifact, raw, artifact.value))
    before = artifact.path.read_bytes()
    result = harnesses.run_harness_command("install", apply=True)
    row = next(row for row in result["items"] if row["path"] == str(artifact.path))
    assert row["status"] == "unmanaged_conflict"
    assert artifact.path.read_bytes() == before
    result = harnesses.run_harness_command("restore", apply=True)
    assert artifact.path.read_bytes() == before


def test_crush_global_configuration_takes_precedence_and_data_is_untouched(profiles):
    home, _, _ = profiles
    global_config = home / ".config/crush/crush.json"
    global_config.parent.mkdir()
    global_config.write_text('{"mcp":{"existing":{"type":"stdio","command":"original"}}}')
    data_path = home / "AppData/Local/crush/crush.json"
    before = data_path.read_bytes()
    harnesses.run_harness_command("install", apply=True)
    assert "jev" in _json(global_config)["mcp"]
    assert data_path.read_bytes() == before


@pytest.mark.parametrize("contents", [
    '{"mcpServers":{"duplicate":{},"duplicate":{}},"secret":"synthetic-secret"}',
    '{"mcpServers":[],"secret":"synthetic-secret"}',
    '{"mcpServers":{},"secret":"synthetic-secret",}',
    'not-json synthetic-secret',
])
def test_invalid_configuration_is_content_free_and_unchanged(profiles, contents):
    home, _, _ = profiles
    path = home / ".commandcode/mcp.json"
    path.write_text(contents)
    result = harnesses.run_harness_command("install", apply=True)
    row = next(row for row in result["items"] if row["path"] == str(path))
    assert row["status"] == "error"
    assert "synthetic-secret" not in json.dumps(result)
    assert path.read_text() == contents


def test_partial_write_failure_retains_original_and_recoverable_manifest(profiles, monkeypatch):
    home, _, _ = profiles
    target = home / ".commandcode/mcp.json"
    before = target.read_bytes()
    real_atomic = harnesses._atomic
    def fail_target(path, raw, private=False):
        if path == target:
            raise OSError("synthetic-secret must never appear")
        return real_atomic(path, raw, private)
    monkeypatch.setattr(harnesses, "_atomic", fail_target)
    result = harnesses.run_harness_command("install", apply=True)
    assert result["status"] == "partial"
    assert target.read_bytes() == before
    assert "synthetic-secret" not in json.dumps(result)
    monkeypatch.setattr(harnesses, "_atomic", real_atomic)
    restored = harnesses.run_harness_command("restore", apply=True)
    assert restored["status"] == "ok"
    assert target.read_bytes() == before


def test_new_file_keeps_unrelated_user_fields_when_restored(profiles):
    home, _, _ = profiles
    path = home / ".cursor/mcp.json"
    path.unlink()
    harnesses.run_harness_command("install", apply=True)
    document = _json(path)
    document["new_user_setting"] = {"keep": True}
    path.write_text(json.dumps(document))
    harnesses.run_harness_command("restore", apply=True)
    assert _json(path) == {"new_user_setting": {"keep": True}}


def test_deleted_entry_is_not_recreated_during_restore(profiles):
    home, _, _ = profiles
    harnesses.run_harness_command("install", apply=True)
    path = home / ".commandcode/mcp.json"
    document = _json(path)
    del document["mcpServers"]["jev"]
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    harnesses.run_harness_command("restore", apply=True)
    assert path.read_bytes() == before


def test_unknown_owned_path_is_never_followed(profiles, tmp_path):
    manifest_dir = RuntimeConfig.load().home / "harness-backups"
    manifest_dir.mkdir(parents=True)
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("retain")
    (manifest_dir / "ownership.json").write_text(json.dumps({"version": 1, "entries": {
        "unknown": {"path": str(unrelated), "kind": "skill", "managed": {"value": "retain"}}}}))
    result = harnesses.run_harness_command("restore", apply=True)
    assert result["unrecognized_managed_targets"] == 1
    assert unrelated.read_text() == "retain"


def test_profile_guidance_becomes_active_only_after_runnable_detection(profiles, monkeypatch):
    home, _, _ = profiles
    result = harnesses.run_harness_command("install", apply=True)
    assert next(item for item in result["harnesses"] if item["name"] == "copilot")["adapter"] == "inactive_guidance"
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: str(home / "copilot.exe") if name == "copilot" else None)
    result = harnesses.run_harness_command("install", apply=True)
    assert next(item for item in result["harnesses"] if item["name"] == "copilot")["adapter"] == "cli_skill"
    assert "inactive setup instructions" not in (home / ".copilot/skills/jev-advice/SKILL.md").read_text()


def test_absent_profiles_are_not_created(tmp_path, monkeypatch, profiles):
    home, _, _ = profiles
    empty = tmp_path / "empty-home"
    empty.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: empty))
    monkeypatch.setenv("LOCALAPPDATA", str(empty / "AppData/Local"))
    monkeypatch.setenv("CODEX_HOME", str(empty / ".codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(empty / ".config"))
    result = harnesses.run_harness_command("install")
    assert result["items"] == []
    assert list(empty.iterdir()) == []


@pytest.mark.parametrize("action", ["remove", "", None])
def test_invalid_action_is_rejected_without_files(profiles, action):
    with pytest.raises(harnesses.HarnessError, match="invalid_harness_action"):
        harnesses.run_harness_command(action, apply=True)
    assert not RuntimeConfig.load().home.exists()
