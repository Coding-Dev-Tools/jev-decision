"""Live diagnostics retain useful results when local accounting is unavailable."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from jev_decision import budget, cli
from jev_decision.client import JevClient
from jev_decision.runtime import RuntimeConfig


def test_setup_rejects_credential_variable_runtime_collision(capsys, tmp_path):
    assert cli.main(["--runtime-home", str(tmp_path / "state"), "setup", "--non-interactive",
        "--credential-source", "env", "--key-env", "JEV_HOME", "--daily-budget", "0"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "credential_variable_conflict"
    assert "TYPESAFE_API_KEY" in result["hint"] and not (tmp_path / "state" / "config.json").exists()


@pytest.mark.parametrize("data", [b"private-caf\xe9", "private-fact".encode("utf-16"), "private-fact".encode("utf-32")])
def test_evidence_encoding_failure_is_actionable_and_preserves_original(tmp_path, capsys, data):
    config = RuntimeConfig(home=tmp_path / "state", workspace_roots=(tmp_path,), daily_budget_usd=0)
    config.save()
    artifact = tmp_path / "producer.log"
    artifact.write_bytes(data)
    assert cli.main(["--runtime-home", str(config.home), "evidence", "--file", str(artifact),
                     "--goal", "Read saved evidence", "--mode", "off", "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "evidence_encoding_not_utf8" and "UTF-8" in result["hint"]
    assert "private-fact" not in json.dumps(result) and artifact.read_bytes() == data
    assert not config.ledger_path.exists()


def test_setup_timezone_error_has_actionable_content_free_hint(capsys):
    assert cli.main(["setup", "--non-interactive", "--credential-source", "env",
                     "--daily-budget", "0", "--timezone", "Definitely/InvalidPrivateZone"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error_code"] == "invalid_timezone" and "--timezone UTC" in result["hint"]
    assert "InvalidPrivateZone" not in json.dumps(result)


def test_unrecognized_local_error_never_exposes_exception_text(monkeypatch, capsys):
    def fail(_cls):
        raise ValueError("private credential-like text")
    monkeypatch.setattr(RuntimeConfig, "load", classmethod(fail))
    assert cli.main(["doctor"]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "unavailable", "error_code": "local_input_or_configuration_error"}


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
