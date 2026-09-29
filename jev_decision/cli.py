"""Managed Jev advice and explicit local producer capture; no permission grants."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .client import JevClient, _decode
from .harness_guards import guard_bash_command, prune_tool_output, verify_turn_completion
from .mcp import MCPServer, local_status, parse_questions, selection_options

_LOCAL_ERRORS = {
    "Unknown timezone; install timezone data or use UTC": ("invalid_timezone", "Install jev-decision[setup] for timezone data, or use --timezone UTC."),
    "Install jev-decision[setup] or choose an environment reference": ("credential_backend_missing", "Install jev-decision[setup], or choose --credential-source env."),
    "OS credential storage is unavailable; choose an environment reference": ("credential_backend_unavailable", "Unlock the OS credential store, or choose --credential-source env."),
    "A supported OS credential backend is required; plaintext backends are refused": ("credential_backend_unsupported", "Use macOS Keychain, Linux Secret Service, or --credential-source env."),
    "Non-interactive setup requires a credential source": ("credential_source_required", "Choose --credential-source env, dpapi, or keyring; or run interactive jev setup."),
    "Non-interactive setup requires an explicit daily budget": ("daily_budget_required", "Set --daily-budget to a nonnegative amount; zero keeps provider requests disabled."),
    "Daily budget must be finite and nonnegative": ("invalid_daily_budget", "Set --daily-budget to a finite nonnegative amount."),
    "relative_harness_location_rejected": ("relative_harness_location_rejected", "Use absolute configuration paths for the selected harness."),
    "project_root_requires_project_scope": ("project_root_requires_project_scope", "Use --scope project with --project-root, or omit --project-root for user scope."),
    "project_scope_unsupported_for_target": ("project_scope_unsupported_for_target", "Use --scope user for this harness."),
    "absolute_project_root_required": ("absolute_project_root_required", "Set --project-root to an existing absolute project directory."),
    "project_root_not_found": ("project_root_not_found", "Set --project-root to an existing project directory."),
    "absolute_new_directory_and_producer_required": ("invalid_capture_arguments", "Use capture --directory ABSOLUTE_NEW_DIRECTORY -- PROGRAM [ARGS...]."),
}


def _print(value):
    print(json.dumps(value, indent=2, allow_nan=False))

def _input(path):
    if path == "-":
        if hasattr(sys.stdin, "buffer"):
            raw = sys.stdin.buffer.read(262145)
            if len(raw) > 262144:
                raise ValueError("input_limit")
            value = raw.decode("utf-8-sig")
        else:
            value = sys.stdin.read(262145)
    else:
        with open(path, encoding="utf-8-sig") as stream:
            value = stream.read(262145)
    if len(value.encode("utf-8")) > 262144:
        raise ValueError("input_limit")
    return value

def main(argv=None):
    parser = argparse.ArgumentParser(prog="jev", description="Managed Jev advisory decisions")
    parser.add_argument("--runtime-home", help="Absolute shared state directory for this invocation")
    commands = parser.add_subparsers(dest="subcommand", required=True)
    from .capture import configure_parser
    capture = commands.add_parser("capture", help="Capture an explicit producer to original files; return only its reference")
    configure_parser(capture)
    setup = commands.add_parser("setup", help="Guide credential source, roots, budget and harness selection")
    setup.add_argument("--non-interactive", action="store_true")
    setup.add_argument("--credential-source", choices=["env", "dpapi", "keyring"])
    setup.add_argument("--key-env")
    setup.add_argument("--workspace", action="append")
    setup.add_argument("--daily-budget")
    setup.add_argument("--timezone")
    setup.add_argument("--harness")
    setup.add_argument("--scope", choices=["user", "project"])
    setup.add_argument("--project-root")
    setup.add_argument("--selection-mode", choices=["off", "shadow"],
                       help="Saved evidence policy; callers may only downgrade it")
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
    prune.add_argument("--mode", choices=["off", "shadow", "select"])
    prune.add_argument("--source-class", choices=["auto", "unknown", "test_log", "build_log", "application_log", "jsonl", "diff"], default="auto")
    evidence = commands.add_parser("evidence", help="Read an approved saved log before context ingestion")
    evidence.add_argument("--file", required=True)
    evidence.add_argument("--goal", required=True)
    evidence.add_argument("--json", action="store_true")
    evidence.add_argument("--mode", choices=["off", "shadow", "select"])
    evidence.add_argument("--source-class", choices=["auto", "unknown", "test_log", "build_log", "application_log", "jsonl", "diff"], default="auto")
    evidence.add_argument("--start-line", type=int, default=1)
    evidence.add_argument("--max-lines", type=int, default=1000)
    evidence.add_argument("--max-bytes", type=int, default=65536)
    evidence.add_argument("--expected-source-sha256")
    evidence.add_argument("--workload", help="JSON file identifying the actual harness, version, primary model and provider")
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
    harness.add_argument("action", choices=["preview", "install", "status", "restore"])
    harness.add_argument("--target", "--harness", help="A selected harness; omitted uses the saved setup selection")
    harness.add_argument("--scope", choices=["user", "project"])
    harness.add_argument("--project-root")
    harness.add_argument("--apply", action="store_true")
    harness.add_argument("--dry-run", action="store_true")
    harness.add_argument("--json", action="store_true")
    commands.add_parser("mcp", help="Run the stdio server")
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "capture":
            from .capture import capture_output
            return capture_output(args.directory, args.command)
        from .runtime import RuntimeConfig
        if args.runtime_home:
            home = Path(args.runtime_home).expanduser()
            if not home.is_absolute():
                raise ValueError("absolute_runtime_home_required")
            os.environ["JEV_HOME"] = str(home.resolve())
        config = RuntimeConfig.load()
        if args.subcommand == "setup":
            from .setup import run_setup
            result = run_setup(interactive=not args.non_interactive, credential_source=args.credential_source,
                key_env=args.key_env, workspaces=args.workspace, daily_budget=args.daily_budget,
                timezone=args.timezone, harness=args.harness, scope=args.scope,
                project_root=args.project_root, selection_mode=args.selection_mode, config=config)
            _print(result)
            return 0 if result.get("status") == "ok" else 2
        if args.subcommand == "mcp":
            MCPServer().run_stdio()
            return 0
        if args.subcommand == "auth":
            from .credentials import credential_status, set_api_key_interactive
            if args.action == "status":
                _print({**credential_status(config), "authenticated": False})
            elif args.gui:
                from .auth_gui import main as gui
                return gui()
            else:
                set_api_key_interactive(config)
                _print({"credential_saved": True})
            return 0
        if args.subcommand == "harness":
            from .harnesses import run_harness_command
            target = args.target or config.harness_target
            if target is None and args.action in {"install", "restore"}:
                raise ValueError("select_harness_target_required")
            scope = args.scope or config.harness_scope
            project_root = args.project_root
            if project_root is None and scope == "project":
                project_root = config.project_root
            result = run_harness_command(args.action, apply=args.apply and not args.dry_run,
                target=target, scope=scope, project_root=project_root, config=config)
            _print(result)
            return 0 if result.get("status") == "ok" else 2
        if args.subcommand == "doctor":
            result = local_status(config=config)
            result["harness"] = args.harness
            if args.live:
                client = JevClient(runtime=config)
                result["live_result"] = client.evaluate(
                    {"message": "The sample log reports a failed unit test."},
                    {"failure_present": {"type": "noul", "instructions": "Does the sample message report a failed unit test?"}}).to_dict()
                result["authenticated"] = result["live_result"]["status"] == "ok" and result["live_result"]["source"] == "provider"
                result["authentication_status"] = "verified" if result["authenticated"] else "failed"
                from .budget import BudgetLedger
                try:
                    result["budget"] = BudgetLedger(config).status()
                except Exception:
                    # Preserve the live response and configuration diagnostics.
                    result["budget"] = {"status": "unavailable"}
                _print(result)
                return 0 if result["authenticated"] else 2
            _print(result)
            return 0
        client = JevClient(runtime=config) if args.subcommand in {"guard", "verify", "decide"} else None
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
            options = selection_options(config, args.mode)
            if options["mode"] != "off":
                client = JevClient(runtime=config)
            if args.workload:
                options["expected_workload"] = _decode(_input(args.workload).encode("utf-8"))
            result = read_evidence_file(args.file, args.goal, config.workspace_roots,
                client=client, source_class=args.source_class, start_line=args.start_line,
                max_lines=args.max_lines, max_bytes=args.max_bytes,
                expected_source_sha256=args.expected_source_sha256, **options)
        else:
            from .policy import sanitize_evidence
            raw = sanitize_evidence(_input(args.file))
            options = selection_options(config, args.mode)
            if options["mode"] != "off":
                client = JevClient(runtime=config)
            output, stats = prune_tool_output(raw, args.goal, client=client,
                source_class=args.source_class, max_retained_lines=args.max_lines, **options)
            if not args.json:
                sys.stdout.write(output)
                if args.stats:
                    sys.stderr.write(json.dumps(stats, allow_nan=False) + "\n")
                return 0
            result = {"output": output, "stats": stats}
        _print(result)
        return 2 if result.get("status") == "unavailable" else 0
    except (ValueError, OSError, UnicodeError, RuntimeError) as error:
        diagnostic = _LOCAL_ERRORS.get(str(error))
        result = {"status": "unavailable", "error_code": "local_input_or_configuration_error"}
        if diagnostic:
            result.update(error_code=diagnostic[0], hint=diagnostic[1])
        _print(result)
        return 2

if __name__ == "__main__":
    sys.exit(main())
