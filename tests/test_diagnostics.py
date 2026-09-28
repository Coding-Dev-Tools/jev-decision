"""Live diagnostics retain useful results when local accounting is unavailable."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from jev_decision import budget, cli
from jev_decision.client import JevClient
from jev_decision.runtime import RuntimeConfig


def test_live_doctor_with_corrupt_ledger_keeps_budget_failure(tmp_path, monkeypatch, capsys):
    config = RuntimeConfig(home=tmp_path, credential_source="env")
    config.save()
    config.ledger_path.write_bytes(b"invalid-sqlite-database")
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "JevClient", lambda **kwargs: JevClient(
        api_key="synthetic-test-key", transport=lambda *_: pytest.fail("Budget failure sent a request"), **kwargs))
    assert cli.main(["doctor", "--live", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["budget"] == {"status": "unavailable"}
    assert result["live_result"]["error_code"] == "budget_unavailable"
    assert result["live_result"]["attempts"] == 0
    assert result["authentication_status"] == "failed"
    assert result["version"] == "0.3.0"
    assert config.ledger_path.read_bytes() == b"invalid-sqlite-database"


def test_live_doctor_keeps_provider_result_when_budget_refresh_fails(tmp_path, monkeypatch, capsys):
    config = RuntimeConfig(home=tmp_path, credential_source="env")
    config.save()
    monkeypatch.setenv("JEV_HOME", str(tmp_path))
    reads = []
    def status(_self, **_kwargs):
        reads.append(True)
        if len(reads) > 1:
            raise sqlite3.OperationalError("database is locked")
        return {"status": "ok"}
    monkeypatch.setattr(budget.BudgetLedger, "status", status)
    # A synthetic receipt isolates the post-request refresh without provider egress.
    receipt = {"status": "ok", "source": "provider", "request_id": "synthetic-test-receipt"}
    fake = SimpleNamespace(evaluate=lambda *_: SimpleNamespace(to_dict=lambda: receipt))
    monkeypatch.setattr(cli, "JevClient", lambda **_: fake)
    assert cli.main(["doctor", "--live", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(reads) == 2
    assert result["budget"] == {"status": "unavailable"}
    assert result["live_result"] == receipt
    assert result["authentication_status"] == "verified"
