"""Guided setup has no provider calls or implicit harness installation."""
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from jev_decision import credentials, harnesses, setup
from jev_decision.runtime import RuntimeConfig, RuntimeConfigError


@pytest.fixture
def setup_home(tmp_path, monkeypatch):
    home = tmp_path / "user home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(harnesses.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData/Local"))
    monkeypatch.setenv("APPDATA", str(home / "AppData/Roaming"))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for name in ("OPENCODE_CONFIG", "CRUSH_GLOBAL_CONFIG", "CRUSH_GLOBAL_DATA", "TEST_JEV_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(credentials, "load_api_key", lambda *a, **kw: pytest.fail("setup read a credential"))
    return home


def test_noninteractive_environment_setup_is_explicit_public_and_offline(setup_home, monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_JEV_KEY", "synthetic-private-value")
    result = setup.run_setup(interactive=False, credential_source="env", key_env="TEST_JEV_KEY",
                             workspaces=[str(tmp_path)], daily_budget="12.50", harness="cursor")
    config = RuntimeConfig.load()
    assert config.enabled and config.setup_complete and config.timezone == "UTC"
    assert config.daily_budget_usd == Decimal("12.50")
    assert config.workspace_roots == (tmp_path,)
    assert config.credential_source == "env" and config.key_env == "TEST_JEV_KEY"
    assert config.harness_target == "cursor" and config.harness_scope == "user"
    assert result["provider_calls"] == 0 and result["provider_authenticated"] is False
    assert result["actual_client_verified"] is False and result["harness_installed"] is False
    assert result["install_args"] == ["--runtime-home", str(config.home), "harness", "install", "--harness", "cursor", "--scope", "user", "--apply"]
    assert not (setup_home / ".cursor").exists()
    assert not config.ledger_path.exists() and not config.credential_path.exists()
    assert "synthetic-private-value" not in json.dumps(result) + config.config_path.read_text()


def test_setup_zero_cap_disables_even_when_environment_key_exists(setup_home, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-key")
    result = setup.run_setup(interactive=False, credential_source="env", daily_budget=0)
    assert result["runtime"]["enabled"] is False
    assert RuntimeConfig.load().setup_complete and not RuntimeConfig.load().enabled
    assert result["harness_preview"] is None and result["install_args"] is None


@pytest.mark.parametrize("options,match", [
    ({"daily_budget": 1}, "credential source"),
    ({"credential_source": "env"}, "explicit daily budget"),
    ({"credential_source": "plaintext", "daily_budget": 1}, "Choose env"),
    ({"credential_source": "env", "daily_budget": "NaN"}, "finite"),
    ({"credential_source": "env", "daily_budget": -1}, "nonnegative"),
    ({"credential_source": "env", "daily_budget": 1, "key_env": "NAME=secret"}, "variable name"),
    ({"credential_source": "env", "daily_budget": 1, "harness": "invalid"}, "Unknown harness"),
    ({"credential_source": "env", "daily_budget": 1, "workspaces": ["relative"]}, "absolute paths"),
])
def test_invalid_or_incomplete_setup_does_not_write(setup_home, options, match):
    with pytest.raises(RuntimeConfigError, match=match):
        setup.run_setup(interactive=False, **options)
    assert not RuntimeConfig.load().home.exists()


def test_project_choice_is_persisted_without_installing(setup_home, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    result = setup.run_setup(interactive=False, credential_source="env", daily_budget="0.20",
                             harness="gemini-cli", scope="project", project_root=project)
    config = RuntimeConfig.load()
    assert config.harness_target == "gemini-cli" and config.harness_scope == "project"
    assert config.project_root == project and list(project.iterdir()) == []
    assert result["install_args"][-3:] == ["--project-root", str(project), "--apply"]
    assert all(Path(item["path"]).is_relative_to(project) for item in result["harness_preview"]["items"])
    repeated = setup.run_setup(interactive=False)
    assert repeated["runtime"]["harness_scope"] == "project"
    assert repeated["runtime"]["project_root"] == str(project)
    assert list(project.iterdir()) == []


def test_reconfiguration_preserves_existing_state_and_evidence_settings(setup_home, tmp_path):
    previous = replace(RuntimeConfig.load(), enabled=True, setup_complete=True, daily_budget_usd="4.25",
                       timezone="America/New_York", credential_source="env", key_env="TEST_JEV_KEY",
                       workspace_roots=(tmp_path,), selection_mode="shadow", harness_target="cursor")
    previous.save()
    previous.ledger_path.write_bytes(b"retained-accounting")
    previous.credential_path.write_bytes(b"retained-protected-credential")
    setup.run_setup(interactive=False)
    current = RuntimeConfig.load()
    assert current.daily_budget_usd == previous.daily_budget_usd and current.timezone == previous.timezone
    assert current.workspace_roots == previous.workspace_roots and current.selection_mode == "shadow"
    assert current.key_env == "TEST_JEV_KEY" and current.harness_target == "cursor"
    assert current.ledger_path.read_bytes() == b"retained-accounting"
    assert current.credential_path.read_bytes() == b"retained-protected-credential"


def test_interactive_defaults_to_disabled_and_prompts_for_no_plaintext_key(setup_home):
    answers = iter(["env", "", "", "", "", ""])
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return next(answers)
    result = setup.run_setup(input_fn=answer)
    assert result["runtime"]["enabled"] is False and result["runtime"]["daily_budget_usd"] == "0"
    assert any("Environment variable" in prompt for prompt in prompts)
    assert not any("API key" in prompt for prompt in prompts)


def test_interactive_cancellation_does_not_save(setup_home):
    def cancelled(prompt):
        raise EOFError
    with pytest.raises(RuntimeConfigError, match="cancelled"):
        setup.run_setup(input_fn=cancelled)
    assert not RuntimeConfig.load().home.exists()


def test_noninteractive_keyring_configures_without_unlocking_or_claiming_presence(setup_home, monkeypatch):
    monkeypatch.setattr(setup, "validate_credential_source", lambda config: {"source": "keyring"})
    monkeypatch.setattr(setup, "set_api_key_interactive", lambda *a: pytest.fail("noninteractive key prompt"))
    result = setup.run_setup(interactive=False, credential_source="keyring", daily_budget=1)
    assert result["credential"]["credential_present"] is None
    assert result["credential"]["presence_status"] == "not_checked"
    assert result["credential_saved"] is False and result["provider_authenticated"] is False


@pytest.mark.parametrize("explicit_source", [False, True])
def test_interactive_reconfiguration_preserves_existing_keyring(setup_home, monkeypatch, tmp_path, explicit_source):
    previous = replace(RuntimeConfig.load(), enabled=True, setup_complete=True,
                       credential_source="keyring", daily_budget_usd="1.00",
                       workspace_roots=(setup_home,), harness_target="cursor")
    previous.save()
    previous.ledger_path.write_bytes(b"retained-accounting")
    monkeypatch.setattr(setup, "validate_credential_source", lambda config: {"source": "keyring"})
    monkeypatch.setattr(setup, "set_api_key_interactive", lambda *_: pytest.fail("existing vault credential overwritten"))
    options = {"credential_source": "keyring"} if explicit_source else {}
    result = setup.run_setup(interactive=True, daily_budget="2.50", workspaces=[tmp_path],
                             harness="command-code", input_fn=lambda _: "", **options)
    current = RuntimeConfig.load()
    assert current.daily_budget_usd == Decimal("2.50")
    assert current.workspace_roots == (tmp_path,) and current.harness_target == "command-code"
    assert current.credential_source == "keyring"
    assert current.ledger_path.read_bytes() == b"retained-accounting"
    assert result["credential"]["credential_present"] is None
    assert result["credential"]["presence_status"] == "not_checked"
    assert result["credential_saved"] is False and result["provider_calls"] == 0
    assert "jev auth set" in result["next_step"]


@pytest.mark.parametrize("existing_source", [None, "env"])
def test_first_keyring_selection_still_offers_masked_entry(setup_home, monkeypatch, existing_source):
    if existing_source:
        replace(RuntimeConfig.load(), setup_complete=True, credential_source=existing_source).save()
    prompted = []
    monkeypatch.setattr(setup, "validate_credential_source", lambda config: {"source": "keyring"})
    monkeypatch.setattr(setup, "set_api_key_interactive", lambda config: prompted.append(config.credential_source))
    result = setup.run_setup(interactive=True, credential_source="keyring", daily_budget=0,
                             timezone="UTC", workspaces=[], harness="cursor")
    assert prompted == ["keyring"]
    assert result["credential_saved"] is True and result["provider_calls"] == 0


def test_invalid_project_is_rejected_before_interactive_key_prompt(setup_home, monkeypatch, tmp_path):
    monkeypatch.setattr(setup, "set_api_key_interactive", lambda *a: pytest.fail("key prompt before validation"))
    with pytest.raises(harnesses.HarnessError, match="project_root_not_found"):
        setup.run_setup(credential_source="keyring", daily_budget=1, timezone="UTC", workspaces=[],
                         harness="cursor", scope="project", project_root=tmp_path / "missing")
    assert not RuntimeConfig.load().home.exists()
