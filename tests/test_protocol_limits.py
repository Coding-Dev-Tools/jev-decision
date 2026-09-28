import json
import subprocess
import sys

from jev_decision.mcp import MAX_MESSAGE_BYTES


def test_duplicate_and_oversized_messages_recover():
    wire = b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n'
    wire += b' ' * (MAX_MESSAGE_BYTES + 4) + b'\n'
    wire += b'{"jsonrpc":"2.0","id":"last","method":"ping"}\n'
    result = subprocess.run([sys.executable, "-m", "jev_decision.mcp"], input=wire,
                            capture_output=True, timeout=10, check=True)
    messages = [json.loads(line) for line in result.stdout.splitlines()]
    assert messages[0]["error"]["code"] == -32700
    assert messages[1]["error"]["code"] == -32600
    assert messages[2] == {"jsonrpc": "2.0", "id": "last", "result": {}}


def test_installed_home_reference_keeps_same_ledger(tmp_path, monkeypatch):
    from jev_decision.runtime import RuntimeConfig
    monkeypatch.delenv("JEV_HOME")
    prefix = tmp_path / "venv"
    prefix.mkdir()
    shared = tmp_path / "physical-user-state"
    (prefix / "jev-runtime-home.txt").write_text(str(shared), encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "different-desktop-view"))
    assert RuntimeConfig.load().ledger_path == shared / "budget.sqlite3"
