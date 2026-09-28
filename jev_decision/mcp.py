"""Standard Model Context Protocol (MCP) server for Jev (TypeSafe AI) System 1 decisions.

Zero external dependencies (pure Python standard library). Operates over stdio JSON-RPC.
Compatible with Cursor, Claude Desktop, Antigravity, Windsurf, Cline, and any MCP client.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List, Optional

from .client import JevClient
from .harness_guards import (
    guard_bash_command,
    prune_tool_output,
    verify_turn_completion,
)
from .primitives import (
    ChoiceQuestion,
    DEFAULT_CALIBRATION,
    NoulQuestion,
    ScoreQuestion,
)

logger = logging.getLogger("jev.mcp")

SERVER_NAME = "jev-decision"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2024-11-05"

TOOLS_MANIFEST = [
    {
        "name": "jev_guard_command",
        "description": "Evaluate the safety of a proposed shell/terminal command before execution in ~100ms. Returns calibrated safety probability, risk category, and whether human approval is required.",
        "inputSchema": {
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell/bash command line to evaluate for safety.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Optional working directory for context.",
                    "default": "",
                },
            },
        },
    },
    {
        "name": "jev_prune_output",
        "description": "Compress bulky command output, test logs, or git diffs by 80-92% before context window insertion by omitting non-relevant passing boilerplate.",
        "inputSchema": {
            "type": "object",
            "required": ["raw_output", "current_goal"],
            "properties": {
                "raw_output": {
                    "type": "string",
                    "description": "The verbose command output or log text to prune.",
                },
                "current_goal": {
                    "type": "string",
                    "description": "The active development/debugging task to measure relevance against.",
                },
                "max_retained_lines": {
                    "type": "integer",
                    "description": "Maximum lines before pruning triggers.",
                    "default": 80,
                },
            },
        },
    },
    {
        "name": "jev_verify_completion",
        "description": "Check if an agent turn genuinely completed its stated goal or requires empirical test/build verification before stopping.",
        "inputSchema": {
            "type": "object",
            "required": ["goal", "recent_actions", "last_output"],
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The user's original task or goal.",
                },
                "recent_actions": {
                    "type": "string",
                    "description": "Summary of actions taken in the current turn.",
                },
                "last_output": {
                    "type": "string",
                    "description": "Terminal output from the last executed test or command.",
                },
            },
        },
    },
    {
        "name": "jev_decide",
        "description": "Execute arbitrary parallel System 1 evaluations (Noul, Choice, Score) against a shared state in a single forward pass.",
        "inputSchema": {
            "type": "object",
            "required": ["state", "questions"],
            "properties": {
                "state": {
                    "type": "string",
                    "description": "The shared context, code snippet, or conversation state to evaluate.",
                },
                "questions": {
                    "type": "array",
                    "description": "List of question objects: {id, prompt, type ('noul'|'choice'|'score'), options?, scale?}",
                    "items": {
                        "type": "object",
                        "required": ["id", "prompt", "type"],
                        "properties": {
                            "id": {"type": "string"},
                            "prompt": {"type": "string"},
                            "type": {"type": "string", "enum": ["noul", "choice", "score"]},
                            "options": {"type": "array", "items": {"type": "string"}},
                            "scale": {"type": "array"},
                        },
                    },
                },
            },
        },
    },
]


class MCPServer:
    """Zero-dependency JSON-RPC stdio MCP Server for Jev."""

    def __init__(self, client: Optional[JevClient] = None) -> None:
        self.client = client or JevClient()

    def handle_request(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        msg_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        # Handle notifications (no id)
        if msg_id is None:
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS_MANIFEST},
            }

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                result_text = self._execute_tool(tool_name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": result_text}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {exc}"}],
                        "isError": True,
                    },
                }

        # Unknown method
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method '{method}' not found"},
        }

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name == "jev_guard_command":
            res = guard_bash_command(
                command=args["command"],
                cwd=args.get("cwd", ""),
                client=self.client,
            )
            return json.dumps(res, indent=2)

        elif name == "jev_prune_output":
            pruned, stats = prune_tool_output(
                raw_output=args["raw_output"],
                current_goal=args["current_goal"],
                max_retained_lines=args.get("max_retained_lines", 80),
                client=self.client,
            )
            return json.dumps({"pruned_output": pruned, "stats": stats}, indent=2)

        elif name == "jev_verify_completion":
            res = verify_turn_completion(
                goal=args["goal"],
                recent_actions=args["recent_actions"],
                last_output=args["last_output"],
                client=self.client,
            )
            return json.dumps(res, indent=2)

        elif name == "jev_decide":
            state = args["state"]
            raw_questions = args["questions"]
            questions = []
            for q in raw_questions:
                q_type = q.get("type", "noul")
                if q_type == "noul":
                    questions.append(NoulQuestion(id=q["id"], prompt=q["prompt"]))
                elif q_type == "choice":
                    questions.append(ChoiceQuestion(id=q["id"], prompt=q["prompt"], options=q.get("options", [])))
                elif q_type == "score":
                    questions.append(ScoreQuestion(id=q["id"], prompt=q["prompt"], scale=q.get("scale", [0, 1, 2, 3, 4])))

            batch = self.client.evaluate(state, questions)
            out = {
                "latency_ms": batch.latency_ms,
                "is_fallback": batch.is_fallback,
                "decisions": {},
            }
            for q_id, dec in batch.decisions.items():
                if hasattr(dec, "probability"):
                    out["decisions"][q_id] = {"probability": dec.probability, "confidence": dec.confidence}
                elif hasattr(dec, "selected"):
                    out["decisions"][q_id] = {"selected": dec.selected, "confidence": dec.confidence, "probabilities": getattr(dec, "probabilities", {})}
                elif hasattr(dec, "score"):
                    out["decisions"][q_id] = {"score": dec.score, "confidence": dec.confidence, "probabilities": getattr(dec, "probabilities", {})}
            return json.dumps(out, indent=2)

        raise ValueError(f"Unknown tool: {name}")

    def run_stdio(self) -> None:
        """Run the stdio message loop."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                resp = self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
            except Exception as exc:
                err_resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()


def main() -> None:
    server = MCPServer()
    server.run_stdio()


if __name__ == "__main__":
    main()
