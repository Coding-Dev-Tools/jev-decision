"""Canonical tool-schema and legacy compatibility checks without provider calls."""

import copy
import json

import pytest

from jev_decision.client import normalize_questions
from jev_decision.mcp import TOOLS_MANIFEST, InvalidParams, MCPServer
from jev_decision.primitives import DecisionBatch


def _schema():
    return next(tool["inputSchema"] for tool in TOOLS_MANIFEST if tool["name"] == "jev_decide")


def _examples():
    schema = _schema()
    return {"state": schema["properties"]["state"]["examples"][0],
            "questions": schema["properties"]["questions"]["examples"][0]}


class RecordingClient:
    def __init__(self):
        self.calls = []

    def evaluate(self, state, questions):
        self.calls.append((state, normalize_questions(questions)))
        return DecisionBatch(status="offline", error_code="offline")


def _call(server, arguments):
    # Exercise the actual JSON encoding boundary used by a native MCP client.
    request = json.loads(json.dumps({
        "jsonrpc": "2.0", "id": "schema-call", "method": "tools/call",
        "params": {"name": "jev_decide", "arguments": arguments},
    }))
    try:
        body = server.call_tool('jev_decide', request['params']['arguments'])
        return {'result': {'content': [{'text': json.dumps(body)}]}}
    except InvalidParams:
        return {'id': 'schema-call', 'error': {'code': -32602}}



def test_advertised_examples_reach_client_as_all_three_native_question_types():
    client = RecordingClient()
    arguments = _examples()
    result = _call(MCPServer(client), arguments)
    assert "error" not in result
    assert json.loads(result["result"]["content"][0]["text"])["status"] == "offline"
    assert len(client.calls) == 1
    state, native = client.calls[0]
    assert state == arguments["state"]
    assert {question["type"] for question in native.values()} == {"noul", "choice", "score"}
    assert native["failure"] == {
        "type": "noul", "instructions": "Does the excerpt report a failed check?",
    }
    assert native["relevance"]["criteria"] == ["Unrelated detail", "Useful context", "Required evidence"]


def test_native_question_map_remains_a_supported_backend_contract():
    client = RecordingClient()
    native = {"finding": {"type": "noul", "instructions": "Does the excerpt report an error?"}}
    result = _call(MCPServer(client), {"state": {"excerpt": "An error occurred"}, "questions": native})
    assert "error" not in result
    assert client.calls[0][1] == native


@pytest.mark.parametrize("transform", [
    lambda questions: json.dumps(questions),
    lambda questions: [],
    lambda questions: [dict(questions[0], type="unsupported")],
    lambda questions: [questions[0], copy.deepcopy(questions[0])],
    lambda questions: [dict(questions[1], criteria=["failure", "success"])],
    lambda questions: [dict(questions[2], criteria=[0, 1, 2])],
])
def test_malformed_questions_are_rejected_before_the_client(transform):
    client = RecordingClient()
    arguments = _examples()
    arguments["questions"] = transform(arguments["questions"])
    result = _call(MCPServer(client), arguments)
    assert result["id"] == "schema-call"
    assert result["error"]["code"] == -32602
    assert client.calls == []


def test_published_schema_is_valid_and_examples_validate():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator
    validator.check_schema(_schema())
    validator(_schema()).validate(_examples())


@pytest.mark.parametrize("replacement", [
    "[{\"id\":\"finding\",\"type\":\"noul\",\"instructions\":\"Question\"}]",
    {},
    [],
    [{"id": "finding", "instructions": "Question"}],
    [{"id": "finding", "type": "unsupported", "instructions": "Question"}],
    [{"id": "finding", "type": "choice", "instructions": "Question"}],
    [{"id": "finding", "type": "score", "instructions": "Question", "criteria": [0, 1]}],
])
def test_published_schema_rejects_common_model_shape_errors(replacement):
    jsonschema = pytest.importorskip("jsonschema")
    arguments = _examples()
    arguments["questions"] = replacement
    assert not jsonschema.Draft202012Validator(_schema()).is_valid(arguments)


def test_published_state_requires_nonempty_supported_json():
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(_schema())
    for value in ("", [], {}, None):
        arguments = _examples()
        arguments["state"] = value
        assert not validator.is_valid(arguments)
