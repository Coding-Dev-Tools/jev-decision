"""Disabled clients must not open a credential vault or managed key file."""
from unittest.mock import Mock

import pytest

from jev_decision.client import JevClient
from jev_decision.primitives import NoulQuestion
from jev_decision.runtime import RuntimeConfig


@pytest.mark.parametrize("source", ["auto", "env", "dpapi", "keyring"])
@pytest.mark.parametrize("disabled", [{"enabled": False}, {"daily_budget_usd": 0}])
def test_disabled_client_never_acquires_a_credential(tmp_path, monkeypatch, source, disabled):
    credential_read = Mock(side_effect=AssertionError("disabled credential access"))
    transport = Mock(side_effect=AssertionError("disabled provider access"))
    monkeypatch.setattr("jev_decision.credentials.load_api_key", credential_read)
    config = RuntimeConfig(home=tmp_path, credential_source=source, **disabled)
    client = JevClient(runtime=config, transport=transport)
    assert client.is_configured is False
    result = client.evaluate("An excerpt", [NoulQuestion("q", "Does the excerpt report an error?")])
    assert result.error_code == "runtime_disabled"
    assert result.attempts == 0
    assert result.decisions == {}
    credential_read.assert_not_called()
    transport.assert_not_called()
    assert not config.ledger_path.exists()
