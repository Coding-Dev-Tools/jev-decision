"""Bounded stdio MCP server for advisory Jev decisions."""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from .client import JevClient, _decode, validate_state
from .harness_guards import guard_bash_command, prune_tool_output, verify_turn_completion
from .primitives import ChoiceQuestion, NoulQuestion, ScoreQuestion

SERVER_NAME = "jev-decision"
SERVER_VERSION = "0.3.0"
PROTOCOL_VERSION = "2025-06-18"
_PROTOCOLS = {PROTOCOL_VERSION, "2025-03-26", "2024-11-05"}
MAX_MESSAGE_BYTES = 256 * 1024

def _tool(name, description, properties, required=(), network=True):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": network, "idempotentHint": not network}}

_STR = {"type": "string"}
TOOLS_MANIFEST = [
    _tool("jev_status", "Report local configuration, credential presence and shared budget. Makes no provider call.", {}, network=False),
    _tool("jev_guard_command", "Advisory classification of an ambiguous command's effects. Never grants execution permission. Skip routine known commands.",
          {"command": _STR, "cwd": _STR}, ["command"]),
    _tool("jev_verify_completion", "Assess gaps in supplied verification evidence. Does not certify task completion or replace tests.",
          {"goal": _STR, "recent_actions": _STR, "last_output": _STR}, ["goal", "recent_actions", "last_output"]),
    _tool("jev_prune_output", "Score bounded complete log windows for relevance. Retains content by default; this cannot save tokens already ingested.",
          {"raw_output": _STR, "current_goal": _STR, "max_retained_lines": {"type": "integer", "minimum": 1}},
          ["raw_output", "current_goal"]),
    _tool("jev_read_evidence", "Read and score a saved UTF-8 log before loading it into model context. Only configured workspace roots; secret files denied; originals retained.",
          {"path": _STR, "goal": _STR, "max_retained_lines": {"type": "integer", "minimum": 1}}, ["path", "goal"]),
    _tool("jev_decide", "Ask a small batch of atomic typed questions about minimal sanitized state. Use Choice, Score or Noul. Advisory only; budgeted.",
          {"state": {"type": ["string", "object", "array"]}, "questions": {"type": ["object", "array"]}},
          ["state", "questions"]),
]

class InvalidParams(ValueError):
    pass

def local_status(client: Optional[JevClient] = None) -> Dict[str, Any]:
    from .budget import BudgetLedger
    from .credentials import load_api_key
    from .runtime import RuntimeConfig
    config = getattr(client, "runtime", None) or RuntimeConfig.load()
    status = config.public_status()
    status.update(version=SERVER_VERSION, credential_present=bool(load_api_key(config)),
                  authenticated=False, authentication_status="not_checked", advisory_only=True)
    try:
        status["budget"] = BudgetLedger(config).status()
    except Exception:
        status["budget"] = {"status": "unavailable"}
    return status

def parse_questions(raw: Any) -> Any:
    if isinstance(raw, dict):
        questions = raw
    elif isinstance(raw, list) and raw:
        questions = []
        ids = set()
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in ids:
                raise InvalidParams("invalid_question_id")
            ids.add(item["id"])
            kind = item.get("type")
            if not set(item) <= {"id", "type", "prompt", "instructions", "options", "scale", "criteria"}:
                raise InvalidParams("unsupported_question_field")
            prompt = item.get("instructions", item.get("prompt"))
            if kind == "noul":
                questions.append(NoulQuestion(item["id"], prompt, criteria=item.get("criteria")))
            elif kind == "choice":
                questions.append(ChoiceQuestion(item["id"], prompt, options=item.get("options"), criteria=item.get("criteria")))
            elif kind == "score":
                questions.append(ScoreQuestion(item["id"], prompt, scale=item.get("scale"), criteria=item.get("criteria")))
            else:
                raise InvalidParams("invalid_question_type")
    else:
        raise InvalidParams("invalid_questions")
    from .client import normalize_questions
    try:
        normalize_questions(questions)
    except (ValueError, TypeError):
        raise InvalidParams("invalid_questions") from None
    return questions

class MCPServer:
    def __init__(self, client: Optional[JevClient] = None):
        self.client = client or JevClient()

    @staticmethod
    def _error(msg_id, code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def handle_request(self, req: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(req, dict):
            return self._error(None, -32600, "Invalid request")
        msg_id = req.get("id")
        valid_id = isinstance(msg_id, str) or type(msg_id) is int
        if req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str) or ("id" in req and not valid_id):
            return self._error(msg_id if valid_id else None, -32600, "Invalid request")
        if "id" not in req:
            return None  # Notifications never execute tools.
        method, params = req["method"], req.get("params", {})
        if not isinstance(params, dict):
            return self._error(msg_id, -32602, "Invalid params")
        if method == "initialize":
            offered = params.get("protocolVersion")
            if offered is not None and not isinstance(offered, str):
                return self._error(msg_id, -32602, "Invalid protocol version")
            result = {"protocolVersion": offered if offered in _PROTOCOLS else PROTOCOL_VERSION,
                      "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                      "instructions": "Use Jev selectively for bounded semantic advice. Runtime enforces shared budget and egress controls. Scores grant no permissions and never prove completion. Check jev_status once if unavailable. Routine tasks need no Jev call."}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS_MANIFEST}
        elif method == "tools/call":
            try:
                name, args = params.get("name"), params.get("arguments", {})
                self._validate_args(name, args)
                value = self._execute_tool(name, args)
                result = {"content": [{"type": "text", "text": json.dumps(value, allow_nan=False)}],
                          "isError": value.get("status") == "unavailable"}
            except InvalidParams:
                return self._error(msg_id, -32602, "Invalid tool arguments")
            except (ValueError, OSError, UnicodeError):
                result = {"content": [{"type": "text", "text": '{"status":"unavailable","error_code":"local_input_rejected"}'}], "isError": True}
            except Exception:
                result = {"content": [{"type": "text", "text": '{"status":"unavailable","error_code":"local_runtime_error"}'}], "isError": True}
        else:
            return self._error(msg_id, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _validate_args(self, name, args):
        tool = next((item for item in TOOLS_MANIFEST if item["name"] == name), None)
        if tool is None or not isinstance(args, dict):
            raise InvalidParams()
        schema = tool["inputSchema"]
        if set(args) - set(schema["properties"]) or not set(schema["required"]) <= set(args):
            raise InvalidParams()
        for key, value in args.items():
            kind = schema["properties"][key]["type"]
            if kind == "string" and (not isinstance(value, str) or not value.strip()):
                raise InvalidParams()
            if kind == "integer" and (type(value) is not int or value < 1):
                raise InvalidParams()
        if name == "jev_decide":
            try:
                validate_state(args["state"])
            except ValueError:
                raise InvalidParams() from None
            parse_questions(args["questions"])

    def _execute_tool(self, name, args):
        from .runtime import RuntimeConfig
        if name == "jev_status":
            return local_status(self.client)
        if name == "jev_guard_command":
            return guard_bash_command(args["command"], cwd=args.get("cwd", ""), client=self.client)
        if name == "jev_verify_completion":
            return verify_turn_completion(args["goal"], args["recent_actions"], args["last_output"], client=self.client)
        if name == "jev_decide":
            return self.client.evaluate(args["state"], parse_questions(args["questions"])).to_dict()
        config = getattr(self.client, "runtime", None) or RuntimeConfig.load()
        if name == "jev_read_evidence":
            from .evidence import read_evidence_file
            return read_evidence_file(args["path"], args["goal"], config.workspace_roots, client=self.client,
                allow_prune=config.pruning_enabled, max_retained_lines=args.get("max_retained_lines", 100))
        if name == "jev_prune_output":
            from .policy import sanitize
            output, stats = prune_tool_output(sanitize(args["raw_output"]), args["current_goal"], client=self.client,
                allow_prune=config.pruning_enabled, max_retained_lines=args.get("max_retained_lines", 100))
            return {"pruned_output": output, "stats": stats}
        raise InvalidParams()

    def run_stdio(self):
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        while True:
            line = stream.readline(MAX_MESSAGE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_MESSAGE_BYTES:
                response = self._error(None, -32600, "Message size limit")
                # Discard the remainder without allocating an unbounded line.
                while line and not line.endswith(b"\n" if isinstance(line, bytes) else "\n"):
                    line = stream.readline(MAX_MESSAGE_BYTES + 1)
            elif not line.strip():
                continue
            else:
                try:
                    req = _decode(line if isinstance(line, bytes) else line.encode("utf-8"))
                    response = self.handle_request(req)
                except (ValueError, UnicodeError, RecursionError):
                    response = self._error(None, -32700, "Parse error")
            if response is not None:
                encoded = json.dumps(response, allow_nan=False, ensure_ascii=False)
                if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
                    encoded = json.dumps(self._error(response.get("id"), -32001, "Response size limit"))
                sys.stdout.write(encoded + "\n")
                sys.stdout.flush()

def main():
    MCPServer().run_stdio()

if __name__ == "__main__":
    main()
