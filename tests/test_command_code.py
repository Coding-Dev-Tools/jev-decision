"""Command Code recipe fixtures and an offline capture-to-CLI example.

These checks do not launch Command Code or verify its UI/tool invocation.
"""
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from jev_decision import credentials, harnesses
from jev_decision.runtime import RuntimeConfig


@pytest.fixture
def command_code_profile(tmp_path, monkeypatch):
    home = tmp_path / "user home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: None)
    monkeypatch.setattr(credentials, "_restrict_acl", lambda *args, **kwargs: None)
    for name, relative in (("LOCALAPPDATA", "AppData/Local"), ("APPDATA", "AppData/Roaming"),
                           ("CODEX_HOME", ".codex"), ("XDG_CONFIG_HOME", ".config")):
        monkeypatch.setenv(name, str(home / relative))
    for name in ("OPENCODE_CONFIG", "CRUSH_GLOBAL_CONFIG", "CRUSH_GLOBAL_DATA", "JEV_COMMAND_CODE_TEST_KEY"):
        monkeypatch.delenv(name, raising=False)
    config = RuntimeConfig(home=tmp_path / "runtime with spaces", credential_source="env",
                           key_env="JEV_COMMAND_CODE_TEST_KEY", daily_budget_usd=0,
                           workspace_roots=(project,))
    return home, project, config


@pytest.mark.parametrize("scope", ["user", "project"])
def test_command_code_installs_manual_skill_and_restores_owned_entries(command_code_profile, monkeypatch, scope):
    home, project, config = command_code_profile
    monkeypatch.setenv(config.key_env, "synthetic-never-persisted")
    root = project if scope == "project" else home
    config_path = project / ".mcp.json" if scope == "project" else home / ".commandcode/mcp.json"
    config_path.parent.mkdir(exist_ok=True)
    original = (b'{"mcpServers":{"existing":{"command":"retain-me"},'
                b'"claude-only":{"type":"stdio","command":"keep-claude"}},"userField":true}\n')
    config_path.write_bytes(original)
    settings = root / ".commandcode/settings.json"
    settings.parent.mkdir(exist_ok=True)
    settings.write_bytes(b'{"permissions":{"mode":"default"},"hooks":{"Stop":[]}}\n')
    mods = root / ".commandcode/mods/user-mod.ts"
    mods.parent.mkdir()
    mods.write_bytes(b"// user-owned mod\n")
    untouched = {path: path.read_bytes() for path in (settings, mods)}
    options = {"target": "command-code", "scope": scope, "config": config}
    if scope == "project":
        options["project_root"] = project
    preview = harnesses.run_harness_command("install", **options)
    assert preview["status"] == "ok" and not config.home.exists()
    assert config_path.read_bytes() == original
    result = harnesses.run_harness_command("install", apply=True, **options)
    assert result["status"] == "ok" and len(result["items"]) == 2
    entry = json.loads(config_path.read_text())["mcpServers"]["jev"]
    assert entry["transport"] == "stdio" and entry["enabled"] is True
    assert entry["command"] == str(Path(sys.executable).resolve())
    assert entry["args"] == ["-I", "-m", "jev_decision.mcp"]
    assert entry["env"] == {"JEV_HOME": str(config.home), config.key_env: "${JEV_COMMAND_CODE_TEST_KEY:-}"}
    skill = root / ".commandcode/skills/jev-advice/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in text
    assert "allowed-tools:" not in text and "disallowed-tools:" not in text
    assert "mcp__jev__jev_read_evidence" in text and "--runtime-home" in text
    assert str(config.home) in text and str(Path(sys.executable).resolve()) in text
    assert "{{" not in text
    assert result["harnesses"][0]["actual_client_verified"] is False
    for path, before in untouched.items():
        assert path.read_bytes() == before
    assert "synthetic-never-persisted" not in config_path.read_text() + text + json.dumps(result)
    assert "synthetic-never-persisted" not in (config.home / "harness-backups/ownership.json").read_text()
    assert harnesses.run_harness_command("restore", apply=True, **options)["status"] == "ok"
    assert config_path.read_bytes() == original and not skill.exists()
    assert all(path.read_bytes() == before for path, before in untouched.items())


def test_command_code_protected_store_and_other_recipes_have_no_env_key_reference(command_code_profile):
    _, _, config = command_code_profile
    artifacts, _ = harnesses._discover("command-code", runtime=replace(config, credential_source="keyring"))
    entry = next(item for item in artifacts.values() if item.kind == "json")
    assert entry.value["env"] == {"JEV_HOME": str(config.home)}
    other, _ = harnesses._discover("antigravity", runtime=config)
    entry = next(item for item in other.values() if item.kind == "json")
    assert entry.value["env"] == {"JEV_HOME": str(config.home)}
    text = next(item.value for item in other.values() if item.kind == "skill")
    assert "disable-model-invocation: true" not in text


def test_modified_command_code_skill_is_preserved_on_restore(command_code_profile):
    home, _, config = command_code_profile
    options = {"target": "command-code", "config": config}
    harnesses.run_harness_command("install", apply=True, **options)
    skill = home / ".commandcode/skills/jev-advice/SKILL.md"
    changed = skill.read_bytes() + b"\nOperator note to retain.\n"
    skill.write_bytes(changed)
    result = harnesses.run_harness_command("restore", apply=True, **options)
    assert result["status"] == "partial"
    assert next(item for item in result["items"] if item["kind"] == "skill")["status"] == "modified_conflict"
    assert skill.read_bytes() == changed


def test_shared_project_entry_changed_by_another_client_is_not_restored(command_code_profile):
    _, project, config = command_code_profile
    options = {"target": "command-code", "scope": "project", "project_root": project, "config": config}
    assert harnesses.run_harness_command("install", apply=True, **options)["status"] == "ok"
    path = project / ".mcp.json"
    document = json.loads(path.read_text())
    document["mcpServers"]["jev"] = {"type": "stdio", "command": "another-clients-runtime"}
    document["claudeSetting"] = "retained"
    path.write_text(json.dumps(document))
    changed = path.read_bytes()
    result = harnesses.run_harness_command("restore", apply=True, **options)
    assert result["status"] == "partial"
    assert next(item for item in result["items"] if item["kind"] == "json")["status"] == "modified_conflict"
    assert path.read_bytes() == changed


@pytest.mark.parametrize("first,second", [("claude-code", "command-code"), ("command-code", "claude-code")])
def test_command_code_shared_project_does_not_transfer_other_clients_ownership(command_code_profile, first, second):
    _, project, config = command_code_profile
    options = {"scope": "project", "project_root": project, "config": config}
    assert harnesses.run_harness_command("install", apply=True, target=first, **options)["status"] == "ok"
    path = project / ".mcp.json"
    installed = path.read_bytes()
    for action in ("install", "status", "restore"):
        result = harnesses.run_harness_command(action, apply=True, target=second, **options)
        assert result["status"] == "partial"
        entry = next(item for item in result["items"] if item["kind"] == "json")
        assert entry["error_code"] == "shared_client_ownership_conflict"
        assert path.read_bytes() == installed
    assert harnesses.run_harness_command("restore", apply=True, target=first, **options)["status"] == "ok"
    assert not path.exists()


def test_command_code_example_capture_then_off_read_never_ingests_raw_output_first(command_code_profile):
    _, project, config = command_code_profile
    repo = Path(__file__).resolve().parents[1]
    destination = project / ".jev-captures/run-001"
    stdout_text, stderr_text = "collected 3 tests\nUnicode 日本語\n", "FAILED test_export\n"
    producer = ("import sys; sys.stdout.buffer.write(" + repr(stdout_text.encode()) + "); "
                "sys.stderr.buffer.write(" + repr(stderr_text.encode()) + "); sys.exit(7)")
    environment = {key: value for key, value in os.environ.items()
                   if key in {"SystemRoot", "WINDIR", "PATH", "TEMP", "TMP", "PATHEXT"}}
    captured = subprocess.run([sys.executable, str(repo / "examples/capture.py"), "--directory", str(destination),
                               "--", sys.executable, "-c", producer], cwd=repo, env=environment,
                              capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert captured.returncode == 7 and captured.stderr == ""
    assert "FAILED test_export" not in captured.stdout and "collected 3 tests" not in captured.stdout
    reference = json.loads(captured.stdout)
    manifest = json.loads(Path(reference["capture"]).read_text())
    assert manifest["producer_exit_status"] == 7
    assert reference["source_sha256"] == hashlib.sha256(Path(reference["capture"]).read_bytes()).hexdigest()
    config.save()
    for name, expected in (("stdout", stdout_text), ("stderr", stderr_text)):
        stream = manifest["streams"][name]
        path = Path(stream["path"])
        original = path.read_bytes()
        assert original == expected.encode("utf-8")
        command = [sys.executable, "-m", "jev_decision.cli", "--runtime-home", str(config.home),
                   "evidence", "--file", str(path), "--goal", "Explain the first failed test", "--mode", "off",
                   "--expected-source-sha256", stream["sha256"], "--max-lines", "200", "--json"]
        result = subprocess.run(command, cwd=repo, env=environment, capture_output=True, text=True,
                                encoding="utf-8", timeout=15)
        assert result.returncode == 0, result.stderr
        body = json.loads(result.stdout)
        assert body["status"] == "ok" and body["output"] == expected
        assert body["source_sha256"] == stream["sha256"] and body["original_preserved"] is True
        assert body["stats"]["mode"] == "off" and body["stats"]["pruned"] is False
        assert path.read_bytes() == original
    assert not config.ledger_path.exists() and not config.credential_path.exists()
