"""Protocol, actual process and CLI contract checks with no provider calls."""
import json
import subprocess
import sys

import pytest

from jev_decision.client import JevClient
from jev_decision.mcp import MCPServer


def request(method, params=None, request_id=7):
    return {"jsonrpc":"2.0", "id":request_id, "method":method, "params":{} if params is None else params}

def test_mcp_discovery_and_advisory_metadata():
    server = MCPServer(JevClient(offline_mode=True))
    result = server.handle_request(request("initialize", {"protocolVersion":"2024-11-05"}))["result"]
    assert result["protocolVersion"] == "2024-11-05"
    tools = server.handle_request(request("tools/list"))["result"]["tools"]
    assert len(tools) == 6
    assert all(tool["annotations"]["destructiveHint"] is False for tool in tools)

@pytest.mark.parametrize("params", [
    {"name":"jev_decide", "arguments":{"state":"sample","questions":[{"id":"x","type":"unsupported","prompt":"q"}]}},
    {"name":"jev_decide", "arguments":{"state":"sample","questions":[{"id":"x","type":"noul","prompt":"q"},{"id":"x","type":"noul","prompt":"q"}]}},
    {"name":"jev_decide", "arguments":{"state":"sample","questions":{"x":{"type":"score","instructions":"q","criteria":[0,1]}}}},
    {"name":"jev_guard_command", "arguments":{"command":44}},
    {"name":"jev_prune_output", "arguments":{"raw_output":"a","current_goal":"g","max_retained_lines":True}},
    {"name":"unknown", "arguments":{}},
])
def test_invalid_arguments_preserve_request_id(params):
    response = MCPServer(JevClient(offline_mode=True)).handle_request(request("tools/call", params, "call-3"))
    assert response["id"] == "call-3"
    assert response["error"]["code"] == -32602

def test_null_params_and_invalid_envelopes():
    server = MCPServer(JevClient(offline_mode=True))
    response = server.handle_request({"jsonrpc":"2.0","id":0,"method":"tools/list","params":None})
    assert response["id"] == 0 and response["error"]["code"] == -32602
    assert server.handle_request([])["error"]["code"] == -32600
    assert server.handle_request({"jsonrpc":"2.0","method":"tools/call","params":{}}) is None

def test_offline_call_is_explicit_not_certification():
    server = MCPServer(JevClient(offline_mode=True))
    response = server.handle_request(request("tools/call", {"name":"jev_verify_completion","arguments":{
        "goal":"all tests passed","recent_actions":"edited","last_output":"tests not run"}}))
    body = json.loads(response["result"]["content"][0]["text"])
    assert body["status"] == "offline" and "is_complete" not in body

def test_real_stdio_process_recovers_after_bad_json():
    wire = "{bad json\n" + json.dumps(request("initialize")) + "\n" + json.dumps(request("tools/list")) + "\n"
    completed = subprocess.run([sys.executable,"-m","jev_decision.mcp"], input=wire,
        text=True, capture_output=True, timeout=10, check=True)
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["result"]["serverInfo"]["version"] == "0.3.0"
    assert len(responses[2]["result"]["tools"]) == 6

def test_cli_doctor_does_not_claim_authentication():
    completed = subprocess.run([sys.executable,"-m","jev_decision.cli","doctor","--json"],
        text=True, capture_output=True, timeout=10, check=True)
    result = json.loads(completed.stdout)
    assert result["authenticated"] is False
    assert "live_result" not in result

def test_cli_invalid_input_is_content_free():
    completed = subprocess.run([sys.executable,"-m","jev_decision.cli","decide"],
        input="secret-sensitive-invalid-json", text=True, capture_output=True, timeout=10)
    assert completed.returncode == 2
    assert "secret-sensitive" not in completed.stdout + completed.stderr
