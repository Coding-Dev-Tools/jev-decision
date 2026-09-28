"""Unit tests for jev_decision MCP server and CLI."""

import json
from jev_decision.client import JevClient
from jev_decision.mcp import MCPServer, PROTOCOL_VERSION, SERVER_NAME


def test_mcp_initialize():
    server = MCPServer(client=JevClient(offline_mode=True))
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {},
    }
    resp = server.handle_request(req)
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == SERVER_NAME


def test_mcp_tools_list():
    server = MCPServer(client=JevClient(offline_mode=True))
    req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }
    resp = server.handle_request(req)
    tools = resp["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    assert "jev_guard_command" in tool_names
    assert "jev_prune_output" in tool_names
    assert "jev_verify_completion" in tool_names
    assert "jev_decide" in tool_names


def test_mcp_tool_call_guard_command():
    server = MCPServer(client=JevClient(offline_mode=True))
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "jev_guard_command",
            "arguments": {"command": "git status", "cwd": "/repo"},
        },
    }
    resp = server.handle_request(req)
    assert resp["result"]["isError"] is False
    content_text = resp["result"]["content"][0]["text"]
    data = json.loads(content_text)
    assert data["allow_auto"] is True
    assert data["safety_probability"] >= 0.95


def test_mcp_tool_call_prune_output():
    server = MCPServer(client=JevClient(offline_mode=True))
    lines = [f"test line {i}" for i in range(120)]
    req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "jev_prune_output",
            "arguments": {
                "raw_output": "\n".join(lines),
                "current_goal": "fixing bug",
                "max_retained_lines": 50,
            },
        },
    }
    resp = server.handle_request(req)
    assert resp["result"]["isError"] is False
    data = json.loads(resp["result"]["content"][0]["text"])
    assert data["stats"]["pruned"] is True


def test_mcp_tool_call_decide():
    server = MCPServer(client=JevClient(offline_mode=True))
    req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "jev_decide",
            "arguments": {
                "state": "COMMAND: git status",
                "questions": [
                    {"id": "q1", "prompt": "Is safe?", "type": "noul"},
                    {"id": "q2", "prompt": "Category?", "type": "choice", "options": ["safe", "destructive"]},
                ],
            },
        },
    }
    resp = server.handle_request(req)
    assert resp["result"]["isError"] is False
    data = json.loads(resp["result"]["content"][0]["text"])
    assert "q1" in data["decisions"]
    assert "q2" in data["decisions"]
