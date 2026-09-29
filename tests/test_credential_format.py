"""Credential setup and the HTTP client agree before any credential is stored."""
import json

import pytest

from jev_decision import cli, credentials
from jev_decision.client import JevClient
from jev_decision.credentials import CredentialError
from jev_decision.runtime import RuntimeConfig

INVALID_KEYS = [
    pytest.param("synthetic-\x1f-key", id="control"),
    pytest.param("synthetic key", id="space"),
    pytest.param("synthetic-\x7f-key", id="del"),
    pytest.param("synthetic-\x80-key", id="non-ascii-control"),
    pytest.param("synthetic-é-key", id="non-ascii-letter"),
    pytest.param("synthetic-😀-key", id="non-ascii-symbol"),
    pytest.param("MoCk", id="reserved-mock"),
    pytest.param("OfFlInE", id="reserved-offline"),
    pytest.param("x" * 4097, id="too-long"),
]


@pytest.mark.parametrize("key", INVALID_KEYS)
@pytest.mark.parametrize("source", ["dpapi", "keyring"])
def test_invalid_key_is_rejected_before_storage(tmp_path, monkeypatch, key, source):
    config = RuntimeConfig(home=tmp_path / "runtime", credential_source=source)
    config.home.mkdir()
    config.credential_path.write_bytes(b"retained-protected-credential")

    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid credential reached a protected store")

    monkeypatch.setattr(credentials, "_dpapi", forbidden)
    monkeypatch.setattr(credentials, "_os_keyring", forbidden)
    with pytest.raises(CredentialError, match="printable ASCII token") as error:
        credentials.save_api_key(key, config)
    assert key not in str(error.value)
    assert config.credential_path.read_bytes() == b"retained-protected-credential"
    assert list(config.home.iterdir()) == [config.credential_path]


@pytest.mark.parametrize("key", INVALID_KEYS)
def test_invalid_environment_key_is_rejected_before_provider_access(tmp_path, monkeypatch, key):
    config = RuntimeConfig(home=tmp_path, credential_source="env", key_env="TEST_JEV_SECRET", enabled=True)
    monkeypatch.setenv(config.key_env, key)
    with pytest.raises(CredentialError, match="printable ASCII token"):
        credentials.load_api_key(config)
    client = JevClient(runtime=config, transport=lambda *_: pytest.fail("Invalid key reached provider"))
    result = client.evaluate("sample", {"q": {"type": "noul", "instructions": "Is this relevant?"}})
    assert result.error_code == "credential_unavailable" and result.attempts == 0
    assert not client.is_configured and not config.ledger_path.exists()
    assert key not in json.dumps(result.to_dict())
    # Direct callers use the same format predicate without changing their input.
    assert not JevClient(api_key=key, runtime=config).is_configured


@pytest.mark.parametrize("key", ["!", "".join(chr(value) for value in range(33, 127)), "x" * 4096],
                         ids=["minimum", "printable-ascii", "maximum"])
def test_accepted_stored_format_is_usable_by_client(tmp_path, key):
    normalized = credentials._validated_key(" \t" + key + "\n")
    assert normalized == key
    assert JevClient(api_key=normalized, runtime=RuntimeConfig(home=tmp_path, enabled=True)).is_configured


def test_interactive_invalid_key_has_safe_actionable_cli_error(tmp_path, monkeypatch, capsys):
    config = RuntimeConfig(home=tmp_path, credential_source="keyring")
    config.save()
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    monkeypatch.setattr(credentials.getpass, "getpass", lambda *_: "synthetic-é-key")
    monkeypatch.setattr(credentials, "_os_keyring", lambda: pytest.fail("Invalid key unlocked the vault"))
    assert cli.main(["auth", "set"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "invalid_credential_format" and "ASCII" in result["hint"]
    assert "synthetic" not in json.dumps(result) and "credential_saved" not in result
