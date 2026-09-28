"""Installer tests use isolated profiles, synthetic contents and no client launches."""
import json
import os
import sys
from dataclasses import replace
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
    assert main(["harness", "install", "--harness", "cursor"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "partial"


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    home = tmp_path / "fake home"
    home.mkdir()
    local = home / "AppData" / "Local"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
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
    environment = {"JEV_HOME": str(RuntimeConfig.load().home)}
    command = _json(home / ".commandcode/mcp.json")["mcpServers"]["jev"]
    assert command == {"transport": "stdio", "enabled": True, "command": python, "args": ["-I", "-m", "jev_decision.mcp"], "env": environment}
    assert _json(home / ".claude.json")["mcpServers"]["jev"]["type"] == "stdio"
    assert _json(home / ".gemini/config/mcp_config.json")["mcpServers"]["jev"] == {"command": python, "args": ["-I", "-m", "jev_decision.mcp"], "env": environment}
    oc = home / ".config/opencode/opencode.jsonc"
    assert _json(oc, True)["mcp"]["jev"] == {"type": "local", "command": [python, "-I", "-m", "jev_decision.mcp"], "enabled": True, "environment": environment}
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
        assert "--runtime-home" in skill and str(RuntimeConfig.load().home) in skill
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
    monkeypatch.setenv("APPDATA", str(empty / "AppData/Roaming"))
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


def test_selected_target_install_and_restore_leave_other_profiles_untouched(profiles):
    home, _, _ = profiles
    before = {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    installed = harnesses.run_harness_command("install", apply=True, target="cursor")
    assert installed["status"] == "ok" and installed["target"] == "cursor"
    assert {client["name"] for client in installed["harnesses"]} == {"cursor"}
    assert all(row["clients"] == ["cursor"] for row in installed["items"])
    for path, raw in before.items():
        if path != str(home / ".cursor/mcp.json"):
            assert Path(path).read_bytes() == raw
    assert not (home / ".codex/skills/jev-advice/SKILL.md").exists()
    client = installed["harnesses"][0]
    assert client["configured"] is True and client["launchable"] is None
    assert client["provider_authenticated"] is False and client["actual_client_verified"] is False
    restored = harnesses.run_harness_command("restore", apply=True, target="cursor")
    assert restored["status"] == "ok"
    assert before == {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()}


def test_selected_restore_keeps_other_owned_target(profiles):
    home, _, _ = profiles
    harnesses.run_harness_command("install", apply=True, target="codex")
    codex_files = {str(path): path.read_bytes() for path in (home / ".codex").rglob("*") if path.is_file()}
    harnesses.run_harness_command("install", apply=True, target="cursor")
    result = harnesses.run_harness_command("restore", apply=True, target="cursor")
    assert result["status"] == "ok" and result["unselected_managed_targets"] == 2
    assert all(Path(path).read_bytes() == raw for path, raw in codex_files.items())
    assert harnesses.run_harness_command("status", target="codex")["harnesses"][0]["configured"]


@pytest.mark.parametrize("target,config_path,skill_path", [
    ("codex", ".codex/config.toml", ".agents/skills"),
    ("claude-code", ".mcp.json", ".claude/skills"),
    ("cursor", ".cursor/mcp.json", ".cursor/skills"),
    ("gemini-cli", ".gemini/settings.json", ".gemini/skills"),
    ("antigravity", ".agents/mcp_config.json", ".agents/skills"),
    ("antigravity-ide", ".agents/mcp_config.json", ".agents/skills"),
    ("opencode", "opencode.jsonc", ".opencode/skills"),
])
def test_project_scope_stays_inside_selected_root_and_restores(profiles, tmp_path, target, config_path, skill_path):
    home, _, _ = profiles
    before = {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    project = tmp_path / "project with spaces"
    project.mkdir()
    options = {"target": target, "scope": "project", "project_root": project}
    preview = harnesses.run_harness_command("install", **options)
    assert preview["status"] == "ok" and list(project.iterdir()) == []
    result = harnesses.run_harness_command("install", apply=True, **options)
    assert result["status"] == "ok"
    assert (project / config_path).is_file()
    assert (project / skill_path / "jev-advice/SKILL.md").is_file()
    assert all(Path(row["path"]).is_relative_to(project) for row in result["items"])
    assert before == {str(path): path.read_bytes() for path in home.rglob("*") if path.is_file()}
    assert harnesses.run_harness_command("status")["status"] == "ok"
    assert harnesses.run_harness_command("restore", apply=True, **options)["status"] == "ok"
    assert not [path for path in project.rglob("*") if path.is_file()]


def test_gemini_native_mcp_preserves_settings(profiles):
    home, _, _ = profiles
    path = home / ".gemini/settings.json"
    original = '{"theme":"Dark","mcpServers":{"other":{"command":"retained"}}}\n'
    path.write_text(original)
    result = harnesses.run_harness_command("install", apply=True, target="gemini-cli")
    assert result["status"] == "ok"
    assert _json(path)["theme"] == "Dark"
    assert _json(path)["mcpServers"]["jev"]["env"] == {"JEV_HOME": str(RuntimeConfig.load().home)}
    assert harnesses.run_harness_command("restore", apply=True, target="gemini-cli")["status"] == "ok"
    assert path.read_text() == original


@pytest.mark.parametrize("scope", ["user", "project"])
@pytest.mark.parametrize("target,user_path,project_path,reference", [
    ("gemini-cli", ".gemini/settings.json", ".gemini/settings.json", "${TEST_JEV_KEY}"),
    ("claude-code", ".claude.json", ".mcp.json", "${TEST_JEV_KEY}"),
    ("cursor", ".cursor/mcp.json", ".cursor/mcp.json", "${env:TEST_JEV_KEY}"),
])
def test_explicit_environment_reference_never_embeds_secret(profiles, monkeypatch, tmp_path, scope,
                                                            target, user_path, project_path, reference):
    home, _, _ = profiles
    config = replace(RuntimeConfig.load(), credential_source="env", key_env="TEST_JEV_KEY")
    monkeypatch.setenv("TEST_JEV_KEY", "synthetic-private-value")
    options = {"target": target, "scope": scope, "config": config}
    root = home
    if scope == "project":
        root = tmp_path / "project"
        root.mkdir()
        options["project_root"] = root
    result = harnesses.run_harness_command("install", apply=True, **options)
    assert result["status"] == "ok"
    path = root / (project_path if scope == "project" else user_path)
    assert _json(path)["mcpServers"]["jev"]["env"] == {
        "JEV_HOME": str(config.home), "TEST_JEV_KEY": reference}
    assert "synthetic-private-value" not in path.read_text() + json.dumps(result)
    manifest = config.home / "harness-backups/ownership.json"
    assert "synthetic-private-value" not in manifest.read_text()
    # Per-client interpolation must not mutate shared stdio recipes.
    artifacts, _ = harnesses._discover("antigravity", runtime=config)
    untouched = next(item for item in artifacts.values() if item.kind == "json")
    assert untouched.value["env"] == {"JEV_HOME": str(config.home)}
    # Protected-store configurations do not add any environment key reference.
    artifacts, _ = harnesses._discover(target, runtime=replace(config, credential_source="keyring"))
    protected = next(item for item in artifacts.values() if item.kind == "json")
    assert protected.value["env"] == {"JEV_HOME": str(config.home)}
    assert harnesses.run_harness_command("restore", apply=True, **options)["status"] == "ok"


@pytest.mark.parametrize("platform,relative", [
    ("win32", "AppData/Roaming/Claude/claude_desktop_config.json"),
    ("darwin", "Library/Application Support/Claude/claude_desktop_config.json"),
])
def test_claude_desktop_supported_platform_recipes(profiles, monkeypatch, platform, relative):
    home, _, _ = profiles
    monkeypatch.setattr(harnesses.sys, "platform", platform)
    result = harnesses.run_harness_command("install", apply=True, target="claude-desktop")
    assert result["status"] == "ok" and len(result["items"]) == 1
    path = home / relative
    assert _json(path)["mcpServers"]["jev"]["env"]["JEV_HOME"] == str(RuntimeConfig.load().home)
    assert result["harnesses"][0]["actual_client_verified"] is False
    assert harnesses.run_harness_command("restore", apply=True, target="claude-desktop")["status"] == "ok"
    assert not path.exists()


def test_claude_desktop_linux_is_explicitly_unsupported(profiles, monkeypatch):
    monkeypatch.setattr(harnesses.sys, "platform", "linux")
    with pytest.raises(harnesses.HarnessError, match="platform_unsupported"):
        harnesses.run_harness_command("install", target="claude-desktop")
    assert not RuntimeConfig.load().home.exists()


def test_codex_forwards_only_environment_reference_and_binds_runtime_home(profiles, monkeypatch):
    home, _, _ = profiles
    config = replace(RuntimeConfig.load(), credential_source="env", key_env="TEST_JEV_KEY")
    monkeypatch.setenv("TEST_JEV_KEY", "synthetic-private-value")
    result = harnesses.run_harness_command("install", apply=True, target="codex", config=config)
    assert result["status"] == "ok"
    text = (home / ".codex/config.toml").read_text()
    entry = harnesses._toml(text)["mcp_servers"]["jev"]
    assert entry["env_vars"] == ["TEST_JEV_KEY"] and entry["env"] == {"JEV_HOME": str(config.home)}
    assert "synthetic-private-value" not in text + json.dumps(result)


@pytest.mark.parametrize("options,error", [
    ({"target": "made-up"}, "unknown_harness"),
    ({"scope": "all"}, "invalid_harness_scope"),
    ({"target": "cursor", "scope": "project", "project_root": "relative"}, "absolute_project_root"),
    ({"target": "claude-desktop", "scope": "project"}, "project_scope_unsupported"),
])
def test_invalid_selected_targets_do_not_write(profiles, options, error):
    with pytest.raises(harnesses.HarnessError, match=error):
        harnesses.run_harness_command("install", apply=True, **options)
    assert not RuntimeConfig.load().home.exists()
