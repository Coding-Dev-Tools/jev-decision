"""Guided local setup: public configuration and credential references, no inference."""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from .credentials import credential_status, set_api_key_interactive, validate_credential_source
from .harnesses import HARNESS_TARGETS, run_harness_command
from .runtime import RuntimeConfig, RuntimeConfigError


def run_setup(*, interactive: bool = True, credential_source: Optional[str] = None,
              key_env: Optional[str] = None, workspaces: Optional[Sequence[str]] = None,
              daily_budget: Any = None, timezone: Optional[str] = None,
              harness: Optional[str] = None, scope: Optional[str] = None, project_root: Any = None,
              selection_mode: Optional[str] = None,
              config: Optional[RuntimeConfig] = None,
              input_fn: Optional[Callable[[str], str]] = None) -> Dict[str, Any]:
    """Configure one runtime and preview one target; installation is separate.

    Non-interactive setup takes an environment-variable *name*, never a key.
    Protected-store keys are entered only through the masked interactive prompt
    or the separate local ``auth set`` flow. No provider authentication occurs.
    """
    if type(interactive) is not bool:
        raise RuntimeConfigError("Interactive setup must be a boolean")
    previous = config or RuntimeConfig.load()
    if selection_mode is not None and (not isinstance(selection_mode, str) or selection_mode not in {"off", "shadow"}):
        raise RuntimeConfigError("Setup selection mode must be off or shadow; select requires a reviewed profile")
    if not interactive and selection_mode is not None and all(value is None for value in (
            credential_source, key_env, workspaces, daily_budget, timezone, harness, scope, project_root)):
        # An operator must be able to disable scoring even with a stale project
        # or unavailable vault. A policy-only change never enables the runtime.
        updated = replace(previous, selection_mode=selection_mode)
        updated.save()
        return {"status": "ok", "configured": updated.setup_complete,
                "setup_complete": updated.setup_complete, "selection_policy_updated": True,
                "runtime": updated.public_status(), "credential_saved": False,
                "provider_authenticated": False, "actual_client_verified": False,
                "provider_calls": 0, "harness_installed": False,
                "next_step": "Restart existing Jev processes to use the saved evidence policy."}
    scope = previous.harness_scope if scope is None else scope
    ask = input_fn or input

    def prompt(label, default=""):
        try:
            value = ask(label + (" [" + str(default) + "]" if default != "" else "") + ": ").strip()
        except (EOFError, KeyboardInterrupt):
            raise RuntimeConfigError("Setup cancelled; configuration was not saved") from None
        return value or default

    if credential_source is None:
        if previous.setup_complete and previous.credential_source != "auto":
            credential_source = previous.credential_source
        elif interactive:
            credential_source = prompt("Credential source (env, dpapi, keyring)", "dpapi" if os.name == "nt" else "keyring")
        else:
            raise RuntimeConfigError("Non-interactive setup requires a credential source")
    if not isinstance(credential_source, str) or credential_source not in {"env", "dpapi", "keyring"}:
        raise RuntimeConfigError("Choose env, dpapi, or keyring for setup")
    if key_env is None:
        key_env = (prompt("Environment variable containing the key", previous.key_env)
                   if interactive and credential_source == "env" else previous.key_env)
    if daily_budget is None:
        if interactive:
            daily_budget = prompt("Daily USD limit (0 disables requests)",
                                  str(previous.daily_budget_usd) if previous.setup_complete else "0")
        elif previous.setup_complete:
            daily_budget = previous.daily_budget_usd
        else:
            raise RuntimeConfigError("Non-interactive setup requires an explicit daily budget")
    if timezone is None:
        timezone = prompt("Budget timezone", previous.timezone) if interactive else previous.timezone
    if workspaces is None:
        workspaces = list(previous.workspace_roots)
        if interactive and not workspaces:
            selected = prompt("Absolute workspace root for saved evidence (blank skips)")
            if selected:
                workspaces = [selected]
    elif not isinstance(workspaces, (tuple, list)):
        raise RuntimeConfigError("Workspace roots must be a list")
    if harness is None:
        harness = previous.harness_target
        if interactive:
            harness = prompt("Harness target (blank skips installation guidance)", harness or "") or None
    if harness is not None and (not isinstance(harness, str) or harness not in HARNESS_TARGETS):
        raise RuntimeConfigError("Unknown harness target")
    if not isinstance(scope, str) or scope not in {"user", "project"}:
        raise RuntimeConfigError("Invalid harness scope")
    if project_root is not None and not isinstance(project_root, (str, Path)):
        raise RuntimeConfigError("Project root must be an absolute path")
    if scope == "project" and project_root is None:
        if interactive:
            project_root = prompt("Absolute project root", str(previous.project_root or ""))
        elif previous.harness_scope == "project":
            project_root = previous.project_root
    if scope == "user" and project_root is not None:
        raise RuntimeConfigError("Project root requires project scope")
    if scope == "project" and harness is None:
        raise RuntimeConfigError("Project scope requires a harness target")

    updated = replace(previous, credential_source=credential_source, key_env=key_env,
                      daily_budget_usd=daily_budget, timezone=timezone,
                      workspace_roots=tuple(workspaces), enabled=True, setup_complete=True,
                      selection_mode=previous.selection_mode if selection_mode is None else selection_mode,
                      harness_target=harness, harness_scope=scope,
                      project_root=Path(project_root) if project_root is not None else None)
    # Validate every public input and selected configuration before key entry or
    # persistence. Preview only reads the explicitly selected target.
    preview = (run_harness_command("install", target=harness, scope=scope,
                                  project_root=updated.project_root, config=updated)
               if harness else None)
    backend = validate_credential_source(updated)
    presence = credential_status(updated)
    credential_saved = False
    if interactive and credential_source in {"dpapi", "keyring"}:
        if presence["credential_present"] is not True:
            set_api_key_interactive(updated)
            credential_saved = True
    updated.save()
    presence = credential_status(updated)
    if credential_saved:
        presence.update(credential_present=True, presence_status="saved")
    install_args = None
    if harness:
        install_args = ["--runtime-home", str(updated.home), "harness", "install", "--harness", harness, "--scope", scope]
        if updated.project_root:
            install_args += ["--project-root", str(updated.project_root)]
        install_args += ["--apply"]
    return {
        "status": "ok", "configured": True, "setup_complete": True,
        "runtime": updated.public_status(), "credential": presence,
        "credential_backend": backend, "credential_saved": credential_saved,
        "provider_authenticated": False, "actual_client_verified": False,
        "provider_calls": 0, "harness_installed": False,
        "harness_preview": preview, "install_args": install_args,
        "next_step": ("Set the referenced variable in the harness launch environment. "
                      if credential_source == "env" else "") +
                     ("Apply the selected harness preview, reload that client, then verify a real client call separately."
                      if harness else "Configure a supported MCP client or use the JSON CLI interface."),
    }
