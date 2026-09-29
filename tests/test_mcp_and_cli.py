"""Official SDK contracts and real UTF-8 subprocesses; no provider calls."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jev_decision.client import JevClient
from jev_decision.mcp import MCPServer, create_sdk_server


def sdk():
    pytest.importorskip('mcp_types', reason='Optional MCP v2 adapter requires Python 3.10+')
    from mcp import Client
    return Client


def test_mcp_discovery_and_advisory_metadata():
    Client = sdk()
    async def check():
        async with Client(create_sdk_server(MCPServer(JevClient(offline_mode=True)))) as client:
            result = await client.list_tools()
            assert len(result.tools) == 6
            assert all(tool.annotations.destructive_hint is False for tool in result.tools)
            assert all(tool.input_schema.get('properties') is not None and tool.output_schema for tool in result.tools)
    asyncio.run(check())


@pytest.mark.parametrize('name,args', [
    ('jev_decide', {'state':'sample','questions':[{'id':'x','type':'unsupported','prompt':'q'}]}),
    ('jev_decide', {'state':'sample','questions':[{'id':'x','type':'noul','prompt':'q'},{'id':'x','type':'noul','prompt':'q'}]}),
    ('jev_decide', {'state':'sample','questions':{'x':{'type':'score','instructions':'q','criteria':[0,1]}}}),
    ('jev_guard_command', {'command':44}),
    ('jev_prune_output', {'raw_output':'a','current_goal':'g','max_retained_lines':True}),
    ('jev_read_evidence', {'path':'x','goal':'g','max_bytes':65537}),
    ('unknown', {}),
])
def test_invalid_arguments_are_protocol_errors(name, args):
    Client = sdk()
    from mcp.shared.exceptions import MCPError
    async def check():
        async with Client(create_sdk_server(MCPServer(JevClient(offline_mode=True)))) as client:
            with pytest.raises(MCPError) as error:
                await client.call_tool(name, args)
            assert error.value.code == -32602
            assert 'sample' not in str(error.value)
    asyncio.run(check())


def test_offline_call_is_explicit_not_certification():
    body = MCPServer(JevClient(offline_mode=True)).call_tool('jev_verify_completion', {
        'goal':'all tests passed','recent_actions':'edited','last_output':'tests not run'})
    assert body['status'] == 'offline' and 'is_complete' not in body


@pytest.mark.parametrize('configured', ['shadow', 'select'])
def test_sdk_omitted_mode_matches_advertised_off_default(configured, tmp_path, monkeypatch):
    Client = sdk()
    from jev_decision import mcp, qualification
    from jev_decision.runtime import RuntimeConfig

    config = RuntimeConfig(home=tmp_path/'runtime', workspace_roots=(tmp_path,), enabled=True,
                           selection_mode=configured, qualified_profile_path=tmp_path/'profile.json')
    config.save()
    monkeypatch.setenv('JEV_HOME', str(config.home))
    def forbidden(*_args, **_kwargs):
        pytest.fail('Omitted SDK mode acquired credentials or loaded a profile')
    monkeypatch.setattr(mcp, 'JevClient', forbidden)
    monkeypatch.setattr(qualification, 'load_qualification', forbidden)
    evidence = tmp_path/'evidence.log'
    source = 'INFO ordinary evidence record\n' * 130
    evidence.write_bytes(source.encode())
    async def check():
        async with Client(create_sdk_server(MCPServer())) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            for name, args, field in (
                ('jev_read_evidence', {'path':str(evidence), 'goal':'inspect'}, 'output'),
                ('jev_prune_output', {'raw_output':source, 'current_goal':'inspect'}, 'pruned_output'),
            ):
                assert tools[name].input_schema['properties']['mode']['default'] == 'off'
                result = (await client.call_tool(name, args)).structured_content
                assert result[field] == source
                assert result['stats']['mode'] == 'off' and result['stats']['calls'] == 0
    asyncio.run(check())


@pytest.mark.parametrize('mode', ['legacy', 'auto', '2026-07-28'])
def test_real_sdk_stdio_client(mode, tmp_path):
    Client = sdk()
    from mcp.client.stdio import StdioServerParameters

    from jev_decision.runtime import RuntimeConfig
    config = RuntimeConfig(home=tmp_path/'runtime', workspace_roots=(tmp_path,), enabled=False)
    config.save()
    evidence = tmp_path/'unicode.log'
    evidence.write_bytes('héllo 日本語 😀\r\nexit status 0\r\n'.encode('utf-8'))
    params = StdioServerParameters(command=sys.executable, args=['-m','jev_decision.mcp'],
        env={**os.environ,'JEV_HOME':str(config.home),'PYTHONIOENCODING':'cp1252'}, cwd=Path(__file__).resolve().parents[1])
    async def check():
        async with Client(params, mode=mode, read_timeout_seconds=10) as client:
            tools = await client.list_tools()
            assert len(tools.tools) == 6
            status = await client.call_tool('jev_status', {})
            assert status.structured_content['authenticated'] is False
            assert status.structured_content['enabled'] is False
            result = await client.call_tool('jev_read_evidence', {'path':str(evidence),'goal':'read Unicode','mode':'off'})
            assert '日本語 😀' in result.structured_content['output']
            result = await client.call_tool('jev_decide', {'state':'bonjour 日本語','questions': {
                'label':{'type':'choice','instructions':'Choose one label','criteria':{'a':None,'b':None}}}})
            assert not result.structured_content['decisions']
            assert result.structured_content['attempts'] == 0
    asyncio.run(check())


def test_core_import_does_not_import_sdk():
    completed = subprocess.run([sys.executable,'-c',
        "import sys; import jev_decision; import jev_decision.cli; assert 'mcp' not in sys.modules; assert 'anyio' not in sys.modules"],
        capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr


def test_cli_doctor_does_not_claim_authentication():
    completed = subprocess.run([sys.executable,'-m','jev_decision.cli','doctor','--json'],
        text=True, capture_output=True, timeout=10, check=True)
    result = json.loads(completed.stdout)
    assert result['authenticated'] is False
    assert 'live_result' not in result


def test_cli_invalid_input_is_content_free():
    completed = subprocess.run([sys.executable,'-m','jev_decision.cli','decide'],
        input='secret-sensitive-invalid-json', text=True, capture_output=True, timeout=10)
    assert completed.returncode == 2
    assert 'secret-sensitive' not in completed.stdout + completed.stderr
