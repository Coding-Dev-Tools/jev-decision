"""Offline runtime/credential/accounting checks, isolated from the user's state."""

import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from jev_decision import budget, credentials, policy
from jev_decision.budget import BudgetError, BudgetExceeded, BudgetLedger
from jev_decision.credentials import CredentialError
from jev_decision.runtime import RuntimeConfig, RuntimeConfigError


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("JEV_HOME", str(tmp_path / "runtime"))
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    return RuntimeConfig.load()


def test_defaults_do_not_create_state(isolated_runtime):
    config = isolated_runtime
    assert not config.home.exists()
    assert config.enabled is False
    assert config.setup_complete is False
    assert config.timezone == "UTC"
    assert config.selection_mode == "off"
    assert config.pruning_enabled is False
    assert config.model == "jev-1.13.0"
    assert config.daily_budget_usd == Decimal("1.00")
    assert config.max_request_bytes == 24576
    assert config.max_response_bytes == 262144
    assert config.workspace_roots == ()


def test_public_config_atomic_roundtrip(isolated_runtime, tmp_path):
    config = RuntimeConfig(home=isolated_runtime.home, workspace_roots=(tmp_path,), daily_budget_usd=Decimal("0.25"))
    config.save()
    assert RuntimeConfig.load() == config
    assert not list(config.home.glob("*.tmp"))
    document = json.loads(config.config_path.read_text())
    assert "api_key" not in document
    assert document["workspace_roots"] == [str(tmp_path.resolve())]
    assert document["version"] == 2
    assert config.public_status()["credential_source"] == "auto"


@pytest.mark.parametrize("changes", [
    {"endpoint": "https://api.typesafe.ai.evil.example/v1/systemone"},
    {"model": "jev-latest"}, {"daily_budget_usd": "-1"}, {"daily_budget_usd": "NaN"},
    {"daily_budget_usd": "0.0000000001"}, {"workspace_roots": ["relative"]},
    {"enabled": "false"}, {"pruning_enabled": 1}, {"timezone": "Missing/Timezone"},
    {"credential_source": "plaintext"}, {"key_env": "KEY=secret"}, {"setup_complete": 1},
    {"key_env": "JEV_HOME"}, {"key_env": "jev_home"},
    {"key_env": "JEV_ENDPOINT_URL"}, {"key_env": "JEV_OFFLINE_MODE"},
    {"selection_mode": "select"}, {"selection_mode": "anything"}, {"qualified_profile_path": "relative"},
    {"max_request_bytes": 24577}, {"max_response_bytes": 262145}, {"timeout_s": float("nan")},
])
def test_invalid_public_configuration_rejected(isolated_runtime, changes):
    with pytest.raises(RuntimeConfigError):
        RuntimeConfig(home=isolated_runtime.home, **changes)


def test_credential_fields_cannot_enter_config(isolated_runtime):
    isolated_runtime.home.mkdir()
    isolated_runtime.config_path.write_text('{"api_key":"synthetic-do-not-print"}')
    with pytest.raises(RuntimeConfigError) as result:
        RuntimeConfig.load()
    assert "synthetic" not in str(result.value)


def test_user_budget_has_no_one_dollar_ceiling_and_zero_disables(tmp_path):
    assert RuntimeConfig(home=tmp_path, daily_budget_usd="12.50").daily_budget_usd == Decimal("12.50")
    assert RuntimeConfig(home=tmp_path, daily_budget_usd=0).enabled is False
    assert RuntimeConfig(home=tmp_path).enabled is True


def test_incomplete_v2_config_does_not_implicitly_enable_provider(isolated_runtime):
    isolated_runtime.home.mkdir()
    isolated_runtime.config_path.write_text('{"version":2}')
    current = RuntimeConfig.load()
    assert current.enabled is False and current.setup_complete is False
    assert current.timezone == "UTC"


@pytest.mark.parametrize("enabled", [True, False])
def test_v1_migration_preserves_state_and_disables_unqualified_selection(isolated_runtime, enabled):
    config = isolated_runtime
    config.home.mkdir()
    original = {"version": 1, "enabled": enabled, "pruning_enabled": True,
                "workspace_roots": [str(config.home.parent)]}
    config.config_path.write_text(json.dumps(original))
    config.ledger_path.write_bytes(b"existing-ledger-marker")
    config.credential_path.write_bytes(b"existing-credential-marker")
    migrated = RuntimeConfig.load()
    assert migrated.enabled is enabled and migrated.setup_complete
    assert migrated.timezone == "America/New_York"
    assert migrated.daily_budget_usd == Decimal("1.00")
    assert migrated.workspace_roots == (config.home.parent,)
    assert migrated.selection_mode == "off" and migrated.pruning_enabled is False
    assert json.loads(config.config_path.read_text()) == original  # load is read-only
    migrated.save()
    assert RuntimeConfig.load() == migrated
    assert config.ledger_path.read_bytes() == b"existing-ledger-marker"
    assert config.credential_path.read_bytes() == b"existing-credential-marker"


def test_legacy_pruning_flag_cannot_enable_selection(tmp_path):
    assert RuntimeConfig(home=tmp_path, pruning_enabled=True).selection_mode == "off"
    assert not RuntimeConfig(home=tmp_path, pruning_enabled=True).pruning_enabled
    selected = RuntimeConfig(home=tmp_path, selection_mode="select", qualified_profile_path=tmp_path / "profile.json")
    assert selected.pruning_enabled is True


def test_selected_environment_source_ignores_other_stores(isolated_runtime, monkeypatch):
    config = replace(isolated_runtime, credential_source="env", key_env="TEST_JEV_SECRET")
    config.home.mkdir()
    config.credential_path.write_bytes(b"not-a-key")
    monkeypatch.setenv("TYPESAFE_API_KEY", "unselected-key")
    monkeypatch.setenv("TEST_JEV_SECRET", "synthetic-selected-key")
    assert credentials.load_api_key(config) == "synthetic-selected-key"
    assert credentials.load_api_key(config, allow_environment=False) is None
    with pytest.raises(CredentialError, match="environment variable"):
        credentials.save_api_key("do-not-write", config)
    assert config.credential_path.read_bytes() == b"not-a-key"


def _fake_keyring(monkeypatch, module="keyring.backends.SecretService", name="Keyring", platform="linux"):
    import types
    values = {}
    backend_type = type(name, (), {"__module__": module, "priority": 5,
        "set_password": lambda self, service, account, value: values.__setitem__((service, account), value),
        "get_password": lambda self, service, account: values.get((service, account))})
    backend = backend_type()
    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_keyring=lambda: backend))
    monkeypatch.setattr(credentials.sys, "platform", platform)
    return values


@pytest.mark.parametrize("module,name,platform", [
    ("keyring.backends.SecretService", "Keyring", "linux"),
    ("keyring.backends.kwallet", "DBusKeyring", "linux"),
    ("keyring.backends.macOS", "Keyring", "darwin"),
    ("keyring.backends.Windows", "WinVaultKeyring", "win32"),
])
def test_approved_os_keyrings_roundtrip_without_plaintext_files(isolated_runtime, monkeypatch, module, name, platform):
    values = _fake_keyring(monkeypatch, module, name, platform)
    config = replace(isolated_runtime, credential_source="keyring")
    credentials.save_api_key("synthetic-vault-key", config)
    assert credentials.load_api_key(config) == "synthetic-vault-key"
    assert len(values) == 1 and not config.home.exists()
    assert credentials.load_api_key(replace(config, home=config.home / "other")) is None


@pytest.mark.parametrize("module,name", [
    ("keyrings.alt.file", "PlaintextKeyring"), ("keyring.backends.null", "Keyring"),
    ("custom.remote", "Keyring"), ("keyring.backends.macOS", "Keyring"),
])
def test_unapproved_keyrings_are_never_read_or_written(isolated_runtime, monkeypatch, module, name):
    values = _fake_keyring(monkeypatch, module, name)
    config = replace(isolated_runtime, credential_source="keyring")
    with pytest.raises(CredentialError, match="supported OS credential backend"):
        credentials.save_api_key("synthetic-key", config)
    with pytest.raises(CredentialError):
        credentials.load_api_key(config)
    assert values == {} and not config.home.exists()


def test_keyring_status_does_not_unlock_the_store(isolated_runtime, monkeypatch):
    monkeypatch.setattr(credentials, "_os_keyring", lambda: pytest.fail("status attempted to access the vault"))
    status = credentials.credential_status(replace(isolated_runtime, credential_source="keyring"))
    assert status["credential_present"] is None and status["presence_status"] == "not_checked"
    assert status["authentication_verified"] is False


def test_environment_key_is_explicit_compatibility(isolated_runtime, monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "synthetic-legacy")
    assert credentials.load_api_key(isolated_runtime) == "synthetic-legacy"
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-primary")
    assert credentials.load_api_key(isolated_runtime) == "synthetic-primary"
    assert credentials.load_api_key(isolated_runtime, allow_environment=False) is None
    status = credentials.credential_status(isolated_runtime)
    assert status["environment_present"] is True
    assert status["authentication_verified"] is False
    assert "synthetic" not in json.dumps(status)


def _mock_vault(monkeypatch):
    vault = {}
    def protect(value, *, decrypt):
        if decrypt:
            return vault[value]
        ciphertext = b"opaque-ciphertext-" + str(len(vault)).encode()
        vault[ciphertext] = value
        return ciphertext
    monkeypatch.setattr(credentials, "_dpapi", protect)
    monkeypatch.setattr(credentials, "_restrict_acl", lambda path, directory: None)


def test_managed_key_priority_and_no_plaintext_storage(isolated_runtime, monkeypatch):
    _mock_vault(monkeypatch)
    credentials.save_api_key("synthetic-managed-value", isolated_runtime)
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-env-value")
    assert credentials.load_api_key(isolated_runtime) == "synthetic-managed-value"
    assert b"synthetic-managed-value" not in isolated_runtime.credential_path.read_bytes()
    assert list(isolated_runtime.home.iterdir()) == [isolated_runtime.credential_path]


def test_atomic_failure_preserves_existing_credential(isolated_runtime, monkeypatch):
    _mock_vault(monkeypatch)
    credentials.save_api_key("synthetic-old-value", isolated_runtime)
    previous = isolated_runtime.credential_path.read_bytes()
    def denied(*args):
        raise PermissionError("simulated replacement failure")
    monkeypatch.setattr(credentials.os, "replace", denied)
    with pytest.raises(CredentialError):
        credentials.save_api_key("synthetic-new-value", isolated_runtime)
    assert isolated_runtime.credential_path.read_bytes() == previous
    assert list(isolated_runtime.home.iterdir()) == [isolated_runtime.credential_path]


def test_corrupt_managed_key_does_not_fall_back_to_environment(isolated_runtime, monkeypatch):
    isolated_runtime.home.mkdir()
    isolated_runtime.credential_path.write_bytes(b"invalid")
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-env-value")
    with pytest.raises(CredentialError):
        credentials.load_api_key(isolated_runtime)


def test_masked_prompt_refuses_echo_fallback(isolated_runtime, monkeypatch):
    def unavailable(prompt):
        raise credentials.getpass.GetPassWarning("no private terminal")
    monkeypatch.setattr(credentials.getpass, "getpass", unavailable)
    with pytest.raises(CredentialError, match="private interactive terminal"):
        credentials.set_api_key_interactive(isolated_runtime)
    assert not isolated_runtime.home.exists()


@pytest.mark.skipif(os.name != "nt", reason="CurrentUser DPAPI is Windows-only")
def test_real_windows_dpapi_with_synthetic_key(isolated_runtime):
    key = "synthetic-test-key-not-a-provider-credential"
    credentials.save_api_key(key, isolated_runtime)
    assert key.encode() not in isolated_runtime.credential_path.read_bytes()
    assert credentials.load_api_key(isolated_runtime, allow_environment=False) == key


def test_accounting_reservation_known_and_unknown_usage(isolated_runtime):
    ledger = BudgetLedger(isolated_runtime)
    first = ledger.reserve()
    assert first.reserved_usd == Decimal("0.002688")
    assert ledger.status()["held_usd"] == 0.002688
    ledger.settle(first, token_count=1000)
    ledger.settle(first, token_count=1000)
    assert ledger.status()["known_spend_usd"] == 0.000042
    second = ledger.reserve()
    ledger.settle(second, token_count=None)
    reloaded = BudgetLedger(isolated_runtime).status()
    assert reloaded["committed_usd"] == 0.00273
    assert reloaded["unknown_attempts"] == 1
    assert reloaded["pending_attempts"] == 0
    assert reloaded["known_spend_usd"] == 0.000042


def test_unknown_usage_is_not_silently_refunded(isolated_runtime):
    config = RuntimeConfig(home=isolated_runtime.home, daily_budget_usd=Decimal("0.002688"))
    ledger = BudgetLedger(config)
    reservation = ledger.reserve()
    ledger.settle(reservation)
    with pytest.raises(BudgetExceeded):
        BudgetLedger(config).reserve()
    assert ledger.status()["remaining_usd"] == 0


def test_concurrent_connections_cannot_overspend(isolated_runtime):
    config = RuntimeConfig(home=isolated_runtime.home, daily_budget_usd=Decimal("0.02688"))
    ledger = BudgetLedger(config)
    def attempt(_):
        try:
            ledger.reserve()
            return True
        except BudgetError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(64)))
    assert sum(results) == 10
    assert ledger.status()["committed_usd"] == 0.02688
    assert ledger.status()["remaining_usd"] == 0


def test_abrupt_process_exit_keeps_reservation(isolated_runtime):
    BudgetLedger(isolated_runtime)
    source = "from jev_decision.budget import BudgetLedger; import os; BudgetLedger().reserve(); os._exit(0)"
    result = subprocess.run([sys.executable, "-B", "-c", source], capture_output=True, timeout=15,
                            cwd=str(Path(__file__).resolve().parents[1]), env=dict(os.environ))
    assert result.returncode == 0
    status = BudgetLedger(isolated_runtime).status()
    assert status["pending_attempts"] == 1
    assert status["held_usd"] == 0.002688


def test_independent_processes_share_one_cap(isolated_runtime):
    config = RuntimeConfig(home=isolated_runtime.home, daily_budget_usd=Decimal("0.02688"))
    config.save()
    ledger = BudgetLedger(config)
    source = """from jev_decision.budget import BudgetLedger, BudgetError
ledger = None
accepted = 0
for _ in range(16):
    try:
        if ledger is None:
            ledger = BudgetLedger()
        ledger.reserve()
        accepted += 1
    except BudgetError:
        pass
print(accepted)
"""
    processes = [subprocess.Popen([sys.executable, "-B", "-c", source], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True,
                                  cwd=str(Path(__file__).resolve().parents[1]), env=dict(os.environ))
                 for _ in range(4)]
    accepted = 0
    for process in processes:
        output, error = process.communicate(timeout=15)
        assert process.returncode == 0, error
        accepted += int(output.strip())
    assert accepted == 10
    assert ledger.status()["committed_usd"] == 0.02688


def test_busy_ledger_fails_closed_with_bounded_sqlite_wait(isolated_runtime, monkeypatch):
    ledger = BudgetLedger(isolated_runtime)
    connect = ledger._connect
    busy_waits = []

    def observe_connection(deadline=None):
        opened = connect(deadline)
        busy_waits.append(opened.execute("PRAGMA busy_timeout").fetchone()[0])
        return opened

    monkeypatch.setattr(ledger, "_connect", observe_connection)
    connection = sqlite3.connect(str(isolated_runtime.ledger_path), isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(BudgetError):
            ledger.reserve()
    finally:
        connection.close()
    # A busy timeout bounds SQLite lock retries, not connection/filesystem work
    # or scheduler delays. Absolute accounting/client deadlines are tested separately.
    assert busy_waits and all(0 < milliseconds <= 200 for milliseconds in busy_waits)
    assert ledger.status()["attempts"] == 0


def test_package_registry_and_typesafe_redaction():
    text = '//registry.npmjs.org/:_authToken=npm_synthetic123456789\n' \
        '_auth=opaque-registry-canary\napikey_synthetic1234567890123456'
    clean = policy.sanitize(text)
    assert "synthetic" not in clean and "opaque-registry-canary" not in clean
    clean_state = policy.sanitize_state({"_authToken": "opaque-value", "_auth": "opaque-value"})
    assert "opaque-value" not in json.dumps(clean_state)


def test_invalid_and_conflicting_usage_remains_conservative(isolated_runtime):
    ledger = BudgetLedger(isolated_runtime)
    reservation = ledger.reserve()
    with pytest.raises(BudgetError):
        ledger.settle(reservation, token_count=True)
    assert ledger.status()["held_usd"] == 0.002688
    ledger.settle(reservation, token_count=100)
    with pytest.raises(BudgetError):
        ledger.settle(reservation, token_count=200)
    ledger.settle(reservation, token_count=None)
    assert ledger.status()["known_spend_usd"] == 0.0000042


def test_provider_usage_above_reservation_is_not_hidden(isolated_runtime):
    ledger = BudgetLedger(isolated_runtime)
    ledger.settle(ledger.reserve(), token_count=100000)
    assert ledger.status()["known_spend_usd"] == 0.0042


def test_midnight_rollover_keeps_old_attempt_on_original_day(isolated_runtime):
    now = [datetime(2026, 9, 28, 3, 59, 59, tzinfo=timezone.utc)]
    ledger = BudgetLedger(replace(isolated_runtime, timezone="America/New_York"), clock=lambda: now[0])
    old = ledger.reserve()
    assert old.day == "2026-09-27"
    now[0] = datetime(2026, 9, 28, 4, 0, 0, tzinfo=timezone.utc)
    ledger.settle(old, token_count=1000)
    assert ledger.status()["attempts"] == 0
    fresh = ledger.reserve()
    assert fresh.day == "2026-09-28"
    assert ledger.status()["known_spend_usd"] == 0


@pytest.mark.parametrize("instant,day,reset", [
    ("2026-03-08T04:59:59+00:00", "2026-03-07", "2026-03-08T05:00:00+00:00"),
    ("2026-03-08T05:00:00+00:00", "2026-03-08", "2026-03-09T04:00:00+00:00"),
    ("2026-03-09T03:59:59+00:00", "2026-03-08", "2026-03-09T04:00:00+00:00"),
    ("2026-11-01T04:00:00+00:00", "2026-11-01", "2026-11-02T05:00:00+00:00"),
    ("2026-11-02T04:59:59+00:00", "2026-11-01", "2026-11-02T05:00:00+00:00"),
    ("2026-11-02T05:00:00+00:00", "2026-11-02", "2026-11-03T05:00:00+00:00"),
])
def test_new_york_dst_without_system_tzdata(isolated_runtime, monkeypatch, instant, day, reset):
    monkeypatch.setattr(budget, "_zone", lambda *args: None)
    ledger = BudgetLedger(replace(isolated_runtime, timezone="America/New_York"), clock=lambda: datetime.fromisoformat(instant))
    status = ledger.status()
    assert status["day"] == day
    assert status["resets_at"] == reset


@pytest.mark.parametrize("endpoint", [
    "http://api.typesafe.ai/v1/systemone", "https://api.typesafe.ai/v1/systemone/",
    "https://api.typesafe.ai/v1/systemone?key=synthetic", "https://api.typesafe.ai.evil.example/v1/systemone",
    "https://api.typesafe.ai@evil.example/v1/systemone", "https://127.0.0.1/v1/systemone",
])
def test_endpoint_is_exactly_allowlisted(endpoint):
    with pytest.raises(policy.PolicyError):
        policy.validate_endpoint(endpoint)


def test_request_byte_boundaries():
    policy.enforce_request_size(b"x" * 24576)
    with pytest.raises(policy.PolicyError):
        policy.enforce_request_size(b"x" * 24577)
    with pytest.raises(policy.PolicyError):
        policy.enforce_request_size(b"x", 24577)


def test_state_redacts_secrets_without_rewriting_normal_fields():
    state = {"api_key": "secret-in-field", "OPENAI_API_KEY": "opaque-other-provider",
             "task": ["TYPESAFE_API_KEY=secret-in-env", "Bearer secret-in-header"],
             "output": "key embedded: opaque-test-key", "token_count": 32, "ok": True}
    clean = policy.sanitize_state(state, secrets=("opaque-test-key",))
    result = json.dumps(clean)
    for secret in ("secret-in-field", "secret-in-env", "secret-in-header", "opaque-test-key", "opaque-other-provider"):
        assert secret not in result
    assert clean["token_count"] == 32
    assert clean["ok"] is True
    assert state["api_key"] == "secret-in-field"


def test_excerpt_redacts_recognizable_credentials():
    text = ('{"password": "some password", "ok": true}\n'
            'https://alice:pword@example.com\n'
            'sk-123456789abcdef\n'
            '-----BEGIN PRIVATE KEY-----\nsecret body\n-----END PRIVATE KEY-----')
    clean = policy.sanitize(text)
    for secret in ("some password", "pword", "alice", "123456789abcdef", "secret body"):
        assert secret not in clean
    assert '"ok": true' in clean


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "bad"}, {"bad": object()}])
def test_non_json_state_fails_closed(value):
    with pytest.raises(policy.PolicyError):
        policy.sanitize_state(value)
