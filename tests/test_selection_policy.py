"""Operator ceilings and credential-free local diagnostics across public routes."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from jev_decision import cli, mcp, qualification, setup
from jev_decision.runtime import RuntimeConfig
from jev_decision.setup import run_setup


@pytest.mark.parametrize("configured", ["off", "shadow", "select"])
@pytest.mark.parametrize("requested", [None, "off", "shadow", "select"])
def test_request_can_only_downgrade_saved_policy(tmp_path, monkeypatch, configured, requested):
    loaded = []
    profile, report = {"test_profile": True}, {"test_report": True}
    def load(path):
        loaded.append(path)
        return profile, report
    monkeypatch.setattr(qualification, "load_qualification", load)
    config = RuntimeConfig(home=tmp_path, selection_mode=configured,
                           qualified_profile_path=tmp_path / "retained-profile.json")
    modes = ["off", "shadow", "select"]
    expected = modes[min(modes.index(configured), modes.index(requested or configured))]
    result = mcp.selection_options(config, requested)
    assert result["mode"] == expected
    assert bool(loaded) is (expected == "select")
    assert ("qualification" in result) is (expected == "select")


@pytest.mark.parametrize("source", ["auto", "dpapi", "keyring"])
def test_status_and_off_reads_never_construct_authenticated_client(tmp_path, monkeypatch, capsys, source):
    config = RuntimeConfig(home=tmp_path / "state", credential_source=source,
                           workspace_roots=(tmp_path,), qualified_profile_path=tmp_path / "retained.json")
    config.save()
    config.credential_path.write_bytes(b"corrupt-or-other-user-protected-value")
    monkeypatch.setenv("JEV_HOME", str(config.home))
    def forbidden(*args, **kwargs):
        pytest.fail("Local diagnostics/off read attempted to unlock a credential")
    monkeypatch.setattr(mcp, "JevClient", forbidden)
    monkeypatch.setattr(cli, "JevClient", forbidden)
    monkeypatch.setattr(qualification, "load_qualification", forbidden)
    server = mcp.MCPServer()
    status = server.call_tool("jev_status", {})
    assert status["authentication_status"] == "not_checked"
    assert status["authenticated"] is False
    assert status["credential_present"] is (None if source == "keyring" else True)
    assert cli.main(["doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["authenticated"] is False
    evidence = tmp_path / "evidence.log"
    evidence.write_bytes(("INFO ordinary evidence record\n" * 130).encode())
    for requested in ("off", "shadow", "select"):
        value = server.call_tool("jev_read_evidence", {"path": str(evidence), "goal": "inspect", "mode": requested})
        assert value["stats"]["mode"] == "off" and value["stats"]["calls"] == 0
        assert value["output"] == evidence.read_text()
        assert cli.main(["evidence", "--file", str(evidence), "--goal", "inspect", "--mode", requested, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["stats"]["mode"] == "off"


def test_shadow_runtime_cannot_load_retained_selection_profile(tmp_path, monkeypatch):
    config = RuntimeConfig(home=tmp_path, selection_mode="shadow", qualified_profile_path=tmp_path / "old.json")
    monkeypatch.setattr(qualification, "load_qualification", lambda *_: pytest.fail("Disabled profile loaded"))
    client = SimpleNamespace(runtime=config, model=config.model)
    # Small evidence bypasses inference, while the returned effective mode is explicit.
    result = mcp.MCPServer(client).call_tool("jev_prune_output", {
        "raw_output": "INFO original record\n", "current_goal": "inspect", "mode": "select"})
    assert result["stats"]["mode"] == "shadow" and result["stats"]["calls"] == 0
    assert result["pruned_output"] == "INFO original record\n"


def test_setup_explicit_shadow_opt_in_and_off_preserve_profile(tmp_path, monkeypatch):
    previous = RuntimeConfig(home=tmp_path, credential_source="env", selection_mode="off",
                             qualified_profile_path=tmp_path / "retained-profile.json")
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    result = run_setup(interactive=False, selection_mode="shadow", config=previous)
    assert result["provider_calls"] == 0
    assert RuntimeConfig.load().selection_mode == "shadow"
    assert cli.main(["setup", "--non-interactive", "--selection-mode", "off"]) == 0
    assert RuntimeConfig.load().selection_mode == "off"
    assert RuntimeConfig.load().qualified_profile_path == previous.qualified_profile_path


@pytest.mark.parametrize("condition", ["stale_project", "unavailable_vault", "disabled", "unconfigured"])
def test_mode_only_update_preserves_runtime_without_unrelated_setup(tmp_path, monkeypatch, capsys, condition):
    previous = RuntimeConfig(home=tmp_path, credential_source="env", selection_mode="shadow")
    if condition == "stale_project":
        previous = replace(previous, harness_target="command-code", harness_scope="project",
                           project_root=tmp_path / "deleted-project")
    elif condition == "unavailable_vault":
        previous = replace(previous, credential_source="keyring")
    elif condition == "disabled":
        previous = replace(previous, enabled=False)
    else:
        previous = replace(previous, enabled=False, setup_complete=False, credential_source="auto")
    previous.save()
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    def forbidden(*_args, **_kwargs):
        pytest.fail("Policy-only update attempted unrelated credential or harness setup")
    for name in ("validate_credential_source", "credential_status", "run_harness_command"):
        monkeypatch.setattr(setup, name, forbidden)
    assert cli.main(["setup", "--non-interactive", "--selection-mode", "off"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["selection_policy_updated"] is True and result["provider_calls"] == 0
    assert RuntimeConfig.load() == replace(previous, selection_mode="off")
