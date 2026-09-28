"""Jev tools served by the optional official MCP SDK; core imports stay lightweight."""
from __future__ import annotations

import functools
import json
import sys
import threading
from typing import Any, Dict, Optional

from .client import JevClient, _decode, normalize_questions, validate_state
from .harness_guards import guard_bash_command, prune_tool_output, verify_turn_completion
from .primitives import ChoiceQuestion, NoulQuestion, ScoreQuestion
from .schemas import TOOLS_MANIFEST

SERVER_NAME = "jev-decision"
SERVER_VERSION = "0.3.0"
MAX_MESSAGE_BYTES = 256 * 1024
INSTRUCTIONS = ("Use Jev selectively for bounded semantic advice. Routine tasks need no Jev call. "
                "Permissions and executed verification remain authoritative. Read saved evidence before "
                "model ingestion. Off makes no scoring calls; shadow measures; select needs a qualified profile.")

class InvalidParams(ValueError):
    pass


def local_status(client: Optional[JevClient] = None, *, config=None) -> Dict[str, Any]:
    from .budget import BudgetLedger
    from .credentials import credential_status
    from .runtime import RuntimeConfig
    config = config or getattr(client, "runtime", None) or RuntimeConfig.load()
    status = config.public_status()
    presence = credential_status(config)
    status.update(version=SERVER_VERSION, credential_present=presence["credential_present"], credential=presence, authenticated=False,
                  authentication_status="not_checked", client_invocation_verified=False, advisory_only=True)
    try:
        status["budget"] = BudgetLedger(config).status()
    except Exception:
        status["budget"] = {"status": "unavailable"}
    return status


def selection_options(config, mode=None):
    """Caller choices can only reduce the operator's saved selection permission."""
    ranks = {"off": 0, "shadow": 1, "select": 2}
    configured = getattr(config, "selection_mode", "off")
    requested = configured if mode is None else mode
    if not isinstance(configured, str) or configured not in ranks:
        raise ValueError("invalid_configured_selection_mode")
    if not isinstance(requested, str) or requested not in ranks:
        raise ValueError("invalid_selection_mode")
    effective = requested if ranks[requested] <= ranks[configured] else configured
    options = {"mode": effective}
    path = getattr(config, "qualified_profile_path", None)
    if options["mode"] == "select" and path:
        from .qualification import load_qualification
        try:
            profile, report = load_qualification(path)
        except (ValueError, OSError):
            return options  # Selection fails closed to unchanged evidence.
        options.update(qualification=profile, qualification_report=report)
    return options

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
    """Application dispatcher. The SDK owns negotiation, framing and protocol errors."""
    def __init__(self, client: Optional[JevClient] = None):
        from .runtime import RuntimeConfig
        self._client = client
        self._client_lock = threading.Lock()
        self.runtime = getattr(client, "runtime", None) or RuntimeConfig.load()

    @property
    def client(self):
        # Discovery, presence-only diagnostics and off reads must not unlock a vault.
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = JevClient(runtime=self.runtime)
        return self._client

    def call_tool(self, name, args):
        names = {tool["name"] for tool in TOOLS_MANIFEST}
        if name not in names or not isinstance(args, dict):
            raise InvalidParams("invalid_tool")
        try:
            if name == "jev_status":
                return local_status(config=self.runtime)
            if name == "jev_guard_command":
                return guard_bash_command(args["command"], cwd=args.get("cwd", ""), client=self.client)
            if name == "jev_verify_completion":
                return verify_turn_completion(args["goal"], args["recent_actions"], args["last_output"], client=self.client)
            if name == "jev_decide":
                validate_state(args["state"])
                questions = parse_questions(args["questions"])
                normalize_questions(questions)
                return self.client.evaluate(args["state"], questions).to_dict()
            config = self.runtime
            options = selection_options(config, args.get("mode"))
            evidence_client = self._client if options["mode"] == "off" else self.client
            options.update(max_retained_lines=args.get("max_retained_lines", 100))
            if "workload" in args:
                options["expected_workload"] = args["workload"]
            if name == "jev_read_evidence":
                from .evidence import read_evidence_file
                for key in ("start_line", "max_lines", "max_bytes", "expected_source_sha256", "source_class"):
                    if key in args:
                        options[key] = args[key]
                return read_evidence_file(args["path"], args["goal"], config.workspace_roots,
                                          client=evidence_client, **options)
            from .policy import sanitize_evidence
            output, stats = prune_tool_output(sanitize_evidence(args["raw_output"]), args["current_goal"],
                                             source_class=args.get("source_class", "auto"), client=evidence_client, **options)
            return {"pruned_output": output, "stats": stats}
        except (KeyError, TypeError, ValueError):
            raise InvalidParams("invalid_tool_arguments") from None

    def run_stdio(self):
        try:
            import anyio
        except ImportError:
            raise RuntimeError("MCP requires Python 3.10+ and pip install 'jev-decision[mcp]'") from None
        server = create_sdk_server(self)
        anyio.run(_serve, server)


def create_sdk_server(service=None):
    """Build the public SDK server without importing MCP for ordinary library users."""
    try:
        import anyio
        import mcp_types as types
        from jsonschema import Draft202012Validator
        from mcp.server import Server
        from mcp.shared.exceptions import MCPError
    except ImportError:
        raise RuntimeError("MCP requires Python 3.10+ and pip install 'jev-decision[mcp]'") from None
    service = service or MCPServer()
    tools = {tool["name"]: tool for tool in TOOLS_MANIFEST}
    validators = {name: Draft202012Validator(tool["inputSchema"]) for name, tool in tools.items()}

    async def list_tools(context, params):
        return types.ListToolsResult(tools=[types.Tool(**tool) for tool in TOOLS_MANIFEST])

    async def call_tool(context, params):
        args = params.arguments or {}
        if params.name not in tools:
            raise MCPError(-32602, "Unknown tool")
        if not validators[params.name].is_valid(args):
            raise MCPError(-32602, "Invalid tool arguments")
        try:
            value = await anyio.to_thread.run_sync(functools.partial(service.call_tool, params.name, args))
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
            wire_size = len(json.dumps({"content": [{"type": "text", "text": encoded}],
                                      "structuredContent": value}, ensure_ascii=False).encode("utf-8"))
            if wire_size > MAX_MESSAGE_BYTES - 4096:
                value = {**{key: value[key] for key in ("source_path", "source_sha256", "page", "original_preserved") if key in value},
                         "status": "unavailable", "error_code": "response_limit"}
        except InvalidParams:
            raise MCPError(-32602, "Invalid tool arguments") from None
        except Exception:
            value = {"status": "unavailable", "error_code": "local_runtime_error"}
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(value, ensure_ascii=False, allow_nan=False))],
                                    structuredContent=value, isError=value.get("status") == "unavailable")

    return Server(SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS,
                  on_list_tools=list_tools, on_call_tool=call_tool)


class _BoundedInput:
    """Bound allocation and remove rejected payloads before handing frames to the SDK."""
    def __init__(self, stream):
        self.stream = stream

    def __aiter__(self):
        return self

    async def __anext__(self):
        import anyio
        while True:
            line = await anyio.to_thread.run_sync(self.stream.readline, MAX_MESSAGE_BYTES + 1)
            if not line:
                raise StopAsyncIteration
            if len(line) > MAX_MESSAGE_BYTES:
                while line and not line.endswith(b"\n"):
                    line = await anyio.to_thread.run_sync(self.stream.readline, MAX_MESSAGE_BYTES + 1)
                return "{rejected frame\n"
            if not line.strip():
                continue
            try:
                _decode(line)
                return line.decode("utf-8")
            except (ValueError, UnicodeError, RecursionError):
                return "{rejected frame\n"


async def _serve(server):
    import io

    import anyio
    from mcp.server.stdio import stdio_server
    # Explicit UTF-8, independent of Windows pipe locale. Protocol remains SDK-owned.
    output = anyio.wrap_file(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n", write_through=True))
    async with stdio_server(stdin=_BoundedInput(sys.stdin.buffer), stdout=output) as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main():
    try:
        MCPServer().run_stdio()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
