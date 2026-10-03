import asyncio
import json
import sys

import pytest

from jev_decision.mcp import MAX_MESSAGE_BYTES


def exchange(rounds):
    """Keep stdin open until replies arrive, as a connected client does."""
    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'jev_decision.mcp',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        responses = []
        try:
            for wire, response_id in rounds:
                process.stdin.write(wire)
                await process.stdin.drain()
                while True:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=15)
                    assert line, 'MCP server exited before its response'
                    response = json.loads(line)
                    responses.append(response)
                    if response.get('id') == response_id:
                        break
            process.stdin.close()
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
            assert process.returncode == 0, stderr
            return responses, stderr
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    return asyncio.run(run())


def test_2024_client_negotiation_and_discovery():
    pytest.importorskip('mcp_types')
    messages = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'legacy-regression', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
    ]
    def encode(items):
        return ('\n'.join(json.dumps(item) for item in items) + '\n').encode()
    responses, _ = exchange([(encode(messages[:1]), 1), (encode(messages[1:]), 2)])
    results = {response['id']: response for response in responses}
    assert results[1]['result']['protocolVersion'] == '2024-11-05'
    assert len(results[2]['result']['tools']) == 6


def test_duplicate_and_oversized_messages_recover():
    pytest.importorskip('mcp_types')
    wire = b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n'
    wire += b' ' * (MAX_MESSAGE_BYTES + 4) + b'\n'
    wire += b'{"jsonrpc":"2.0","id":"last","method":"ping"}\n'
    messages, stderr = exchange([(wire, 'last')])
    # The maintained SDK discards malformed frames; it owns error semantics.
    assert not any(message.get('id') in (1, 2) for message in messages)
    assert any(message.get('id') == 'last' and message.get('result') == {} for message in messages)
    assert 'credential' not in stderr.decode('utf-8').lower()


def test_installed_home_reference_keeps_same_ledger(tmp_path, monkeypatch):
    from jev_decision.runtime import RuntimeConfig
    monkeypatch.delenv('JEV_HOME')
    prefix = tmp_path / 'venv'
    prefix.mkdir()
    shared = tmp_path / 'physical-user-state'
    (prefix / 'jev-runtime-home.txt').write_text(str(shared), encoding='utf-8')
    monkeypatch.setattr(sys, 'prefix', str(prefix))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'different-desktop-view'))
    assert RuntimeConfig.load().ledger_path == shared / 'budget.sqlite3'
