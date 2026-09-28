"""CLI for managed, budgeted Jev advice; no command execution or approval."""
from __future__ import annotations

import argparse
import json
import sys

from .client import JevClient, _decode
from .harness_guards import guard_bash_command, prune_tool_output, verify_turn_completion
from .mcp import MCPServer, local_status, parse_questions


def _print(value):
    print(json.dumps(value, indent=2, allow_nan=False))

def _input(path):
    if path == "-":
        value = sys.stdin.read(262145)
    else:
        with open(path, encoding="utf-8-sig") as stream:
            value = stream.read(262145)
    if len(value.encode("utf-8")) > 262144:
        raise ValueError("input_limit")
    return value

def main(argv=None):
    parser = argparse.ArgumentParser(prog="jev", description="Managed Jev advisory decisions")
    commands = parser.add_subparsers(dest="subcommand", required=True)
    guard = commands.add_parser("guard", help="Assess command risk; never execute or authorize")
    guard.add_argument("command")
    guard.add_argument("--cwd", default="")
    guard.add_argument("--json", action="store_true")
    verify = commands.add_parser("verify", help="Assess evidence gaps; never certify completion")
    verify.add_argument("--goal", required=True)
    verify.add_argument("--actions", required=True)
    verify.add_argument("--output", required=True)
    verify.add_argument("--json", action="store_true")
    prune = commands.add_parser("prune", help="Score log relevance; preserves content by default")
    prune.add_argument("--goal", required=True)
    prune.add_argument("--file", default="-")
    prune.add_argument("--max-lines", type=int, default=100)
    prune.add_argument("--stats", action="store_true")
    prune.add_argument("--json", action="store_true")
    evidence = commands.add_parser("evidence", help="Read an approved saved log before context ingestion")
    evidence.add_argument("--file", required=True)
    evidence.add_argument("--goal", required=True)
    evidence.add_argument("--json", action="store_true")
    decide = commands.add_parser("decide", help="Read JSON {state,questions} from file/stdin")
    decide.add_argument("--file", default="-")
    doctor = commands.add_parser("doctor", help="Local checks; --live sends one synthetic budgeted request")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--live", action="store_true")
    doctor.add_argument("--harness", default="direct")
    auth = commands.add_parser("auth", help="Store a credential via masked local entry")
    auth.add_argument("action", choices=["set", "status"])
    auth.add_argument("--gui", action="store_true")
    harness = commands.add_parser("harness", help="Preview/apply/restore managed harness integration")
    harness.add_argument("action", choices=["install", "status", "restore"])
    harness.add_argument("--apply", action="store_true")
    harness.add_argument("--dry-run", action="store_true")
    harness.add_argument("--json", action="store_true")
    commands.add_parser("mcp", help="Run the stdio server")
    args = parser.parse_args(argv)
    try:
        from .runtime import RuntimeConfig
        config = RuntimeConfig.load()
        if args.subcommand == "mcp":
            MCPServer().run_stdio()
            return 0
        if args.subcommand == "auth":
            from .credentials import load_api_key, set_api_key_interactive
            if args.action == "status":
                _print({"credential_present": bool(load_api_key(config)), "authenticated": False})
            elif args.gui:
                from .auth_gui import main as gui
                return gui()
            else:
                set_api_key_interactive(config)
                _print({"credential_saved": True})
            return 0
        if args.subcommand == "harness":
            from .harnesses import run_harness_command
            result = run_harness_command(args.action, apply=args.apply and not args.dry_run)
            _print(result)
            return 0 if result.get("status") == "ok" else 2
        client = JevClient(runtime=config)
        if args.subcommand == "doctor":
            result = local_status(client)
            result["harness"] = args.harness
            if args.live:
                result["live_result"] = client.evaluate(
                    {"message": "The sample log reports a failed unit test."},
                    {"failure_present": {"type": "noul", "instructions": "Does the sample message report a failed unit test?"}}).to_dict()
                result["authenticated"] = result["live_result"]["status"] == "ok" and result["live_result"]["source"] == "provider"
                result["authentication_status"] = "verified" if result["authenticated"] else "failed"
                from .budget import BudgetLedger
                result["budget"] = BudgetLedger(config).status()
                _print(result)
                return 0 if result["authenticated"] else 2
            _print(result)
            return 0
        if args.subcommand == "guard":
            result = guard_bash_command(args.command, cwd=args.cwd, client=client)
        elif args.subcommand == "verify":
            result = verify_turn_completion(args.goal, args.actions, args.output, client=client)
        elif args.subcommand == "decide":
            data = _decode(_input(args.file).encode("utf-8"))
            if not isinstance(data, dict) or set(data) != {"state", "questions"}:
                raise ValueError("invalid_decision_input")
            result = client.evaluate(data["state"], parse_questions(data["questions"])).to_dict()
        elif args.subcommand == "evidence":
            from .evidence import read_evidence_file
            result = read_evidence_file(args.file, args.goal, config.workspace_roots,
                client=client, allow_prune=config.pruning_enabled)
        else:
            from .policy import sanitize
            raw = sanitize(_input(args.file))
            output, stats = prune_tool_output(raw, args.goal, client=client,
                allow_prune=config.pruning_enabled, max_retained_lines=args.max_lines)
            if not args.json:
                sys.stdout.write(output)
                if args.stats:
                    sys.stderr.write(json.dumps(stats, allow_nan=False) + "\n")
                return 0
            result = {"output": output, "stats": stats}
        _print(result)
        return 2 if result.get("status") == "unavailable" else 0
    except (ValueError, OSError, UnicodeError, RuntimeError):
        _print({"status": "unavailable", "error_code": "local_input_or_configuration_error"})
        return 2

if __name__ == "__main__":
    sys.exit(main())
