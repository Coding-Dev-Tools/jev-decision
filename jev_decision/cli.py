"""Command-line interface (CLI) for Jev System 1 decisions and guardrails.

Usage:
    jev guard "git status"
    jev prune --goal "fix authentication bug" < test_output.log
    jev verify --goal "fix bug" --actions "ran tests" --output "100% green"
    jev mcp   # start stdio MCP server
"""

from __future__ import annotations

import argparse
import json
import sys

from .client import JevClient
from .harness_guards import (
    guard_bash_command,
    prune_tool_output,
    verify_turn_completion,
)
from .mcp import MCPServer


def cmd_guard(args: argparse.Namespace) -> int:
    res = guard_bash_command(args.command, cwd=args.cwd)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        status = "ALLOWED (Auto-Execute)" if res["allow_auto"] else "BLOCKED (Requires Approval)"
        print(f"[{status}] Category: {res['category']} | Safety: {res['safety_probability']:.2f}")
    return 0 if res["allow_auto"] else 1


def cmd_prune(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
    pruned, stats = prune_tool_output(raw, current_goal=args.goal, max_retained_lines=args.max_lines)
    if args.stats:
        sys.stderr.write(f"Saved lines: {stats['saved_lines']} / {stats.get('original_lines', 0)} ({stats.get('token_savings_est', 0)} tokens est.)\n")
    sys.stdout.write(pruned + "\n")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    res = verify_turn_completion(args.goal, args.actions, args.output)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        status = "COMPLETE" if res["is_complete"] else "INCOMPLETE / UNVERIFIED"
        print(f"[{status}] Probability: {res['completion_probability']:.2f} | Needs verify: {res['needs_verification_run']}")
    return 0 if res["is_complete"] else 1


def cmd_mcp(args: argparse.Namespace) -> int:
    server = MCPServer()
    server.run_stdio()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="jev", description="Jev System 1 Decision & Guardrail CLI")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # guard
    p_guard = subparsers.add_parser("guard", help="Evaluate bash command safety")
    p_guard.add_argument("command", help="Command line to evaluate")
    p_guard.add_argument("--cwd", default="", help="Working directory context")
    p_guard.add_argument("--json", action="store_true", help="Output JSON")
    p_guard.set_defaults(func=cmd_guard)

    # prune
    p_prune = subparsers.add_parser("prune", help="Token-prune bulky logs or diffs")
    p_prune.add_argument("--goal", required=True, help="Current goal/task")
    p_prune.add_argument("--file", default="-", help="Input file path or '-' for stdin")
    p_prune.add_argument("--max-lines", type=int, default=80, help="Line threshold")
    p_prune.add_argument("--stats", action="store_true", help="Print savings stats to stderr")
    p_prune.set_defaults(func=cmd_prune)

    # verify
    p_verify = subparsers.add_parser("verify", help="Verify turn completion")
    p_verify.add_argument("--goal", required=True, help="Stated goal")
    p_verify.add_argument("--actions", required=True, help="Recent actions")
    p_verify.add_argument("--output", required=True, help="Last command output")
    p_verify.add_argument("--json", action="store_true", help="Output JSON")
    p_verify.set_defaults(func=cmd_verify)

    # mcp
    p_mcp = subparsers.add_parser("mcp", help="Start stdio MCP server")
    p_mcp.set_defaults(func=cmd_mcp)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
