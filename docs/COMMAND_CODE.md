# Command Code: guard shell commands and read saved output before loading it

Quick path, after `jev setup`:

```sh
jev harness install --target command-code --apply   # jev MCP server + /jev-advice skill
jev hook config command-code                         # optional pre-execution shell guard (merge into settings.json)
```

The Command Code integration installs a focused, explicitly invoked `/jev-advice` skill and the `jev` MCP entry. It guides the agent to read approved saved logs through `jev_read_evidence` before those logs enter model context. Installation and `off` reads make no Jev provider requests. Shadow scoring and qualified selection require separate operator opt-in; no startup or post-tool hook runs inference automatically.

## Install and restore

Use an installed Python with `jev-decision[mcp]`; `-I` requires the package to be installed into that exact interpreter. The following PowerShell example uses project scope and starts with a zero budget. Replace the three absolute paths with your own:

```powershell
$jevPython = 'C:/tools/jev/Scripts/python.exe'
$jevState = 'C:/Users/you/AppData/Local/JevDecision'
$projectRoot = 'C:/work/example'
& $jevPython -I -m jev_decision.cli --runtime-home $jevState setup --non-interactive --credential-source env --key-env TYPESAFE_API_KEY --workspace $projectRoot --daily-budget 0 --selection-mode off --harness command-code --scope project --project-root $projectRoot
& $jevPython -I -m jev_decision.cli --runtime-home $jevState harness install --target command-code --scope project --project-root $projectRoot --dry-run
& $jevPython -I -m jev_decision.cli --runtime-home $jevState harness install --target command-code --scope project --project-root $projectRoot --apply
```

On macOS/Linux, use `/absolute/venv/bin/python` and ordinary shell invocation without PowerShell's `&`. For user scope, use `--scope user` and omit `--project-root` from each command.

| Scope | MCP entry | Managed skill |
| --- | --- | --- |
| User | `~/.commandcode/mcp.json` → `mcpServers.jev` | `~/.commandcode/skills/jev-advice/SKILL.md` |
| Project | `<project>/.mcp.json` → `mcpServers.jev` | `<project>/.commandcode/skills/jev-advice/SKILL.md` |

Command Code's private local scope can override project and user MCP entries. Project `.mcp.json` may also be consumed by another client, so review the preview before applying. The installer preserves unrelated entries and refuses to adopt an unmanaged `jev` entry. These locations and precedence are documented in [Command Code MCP](https://commandcode.ai/docs/mcp#configuration--scopes).

If the shared project's `jev` entry is already managed for Claude Code, Command Code installation/restoration reports `shared_client_ownership_conflict`; the reverse order is also protected. Use user scope for independent client configurations, or have the operator reconcile a shared entry. An external edit to an owned entry remains a conflict rather than being silently restored.

The generated entry binds an absolute interpreter and `JEV_HOME`. An environment credential uses `${TYPESAFE_API_KEY:-}` (or your selected variable name), never its value. The empty fallback allows credential-free off reads. Supply the real variable in the client launch environment only when enabling provider use, or choose the supported OS vault in `jev setup`. The installed skill's CLI command also binds the absolute runtime home.

To restore this project integration, preview first, then apply:

```powershell
& $jevPython -I -m jev_decision.cli --runtime-home $jevState harness restore --target command-code --scope project --project-root $projectRoot --dry-run
& $jevPython -I -m jev_decision.cli --runtime-home $jevState harness restore --target command-code --scope project --project-root $projectRoot --apply
```

Restoration uses saved ownership records, retains unrelated edits, and reports conflicts instead of overwriting changed managed content. It does not erase the runtime, credentials, budget ledger, or captured evidence. A user-scope restore uses `--scope user` with no project-root argument.

## Invoke the workflow

Reload the client, inspect `/mcp` for the `jev` connection, and inspect `/skills` for `jev-advice`. Complete any normal workspace trust or tool approval prompts. The skill sets `disable-model-invocation: true`; invoke it explicitly instead of expecting automatic discovery from the model's skill catalog. [Command Code skills](https://commandcode.ai/docs/skills#skills-specification)

For an existing saved log, type a path as ordinary text, without attaching the entire file:

```text
/jev-advice C:/work/example/.jev-captures/run-001/stderr.log Explain the first failed test. Use off mode; do not rerun the producer.
```

The skill directs the agent to call `mcp__jev__jev_read_evidence` with a bounded page. The equivalent direct CLI call is:

```powershell
& $jevPython -I -m jev_decision.cli --runtime-home $jevState evidence --file 'C:/work/example/.jev-captures/run-001/stderr.log' --goal 'Explain the first failed test' --mode off --max-lines 200 --json
```

For a command that has not run, use the installed `capture` subcommand through the ordinary shell tool, under the same permissions as the producer. No repository checkout is needed. It executes an argv command without a shell and saves stdout, stderr, exit status, sizes and hashes. This synthetic example demonstrates a failed producer while returning only a capture reference:

```powershell
& $jevPython -I -m jev_decision.cli capture --directory 'C:/work/example/.jev-captures/run-001' -- $jevPython -c 'import sys; print("collected 3 tests"); print("FAILED test_export", file=sys.stderr); sys.exit(7)'
```

Use a new capture directory for each run. Retain the helper's exit status `7` and `capture.json`; the helper does not reinterpret success. Read both saved streams when relevant. Never pipe the original log through the agent and then claim later scoring saved its context tokens. For output above the evidence reader's file limit, retain the original and produce bounded, provenance-preserving chunks before reading; a truncated excerpt is not a complete run.

## Off, shadow, and qualified select

`off` is the initial policy. To run an explicitly authorized shadow trial, first choose a nonzero daily cap and save the mode. For example, the operator can repeat setup with `--daily-budget 0.25 --selection-mode shadow` after approving that cap and configuring credentials. A later evidence call may request `--mode shadow`; it retains the entire sanitized page while collecting scoring metadata. Per-call options cannot upgrade a saved `off` policy.

`select` requires a reviewed profile configured by the operator and [qualification evidence](EVALUATION.md). Pass an independently established workload identity with the real `harness`, `harness_version`, `primary_model`, and `primary_provider`; use MCP's `workload` object or CLI `--workload /absolute/workload.json`. Do not fill these fields by copying a profile. No Command Code selection profile ships, and no saved policy is upgraded by this skill.

Keep `source_sha256` and page metadata. Retrieve an omitted or later range with `--mode off --start-line N --max-lines M --expected-source-sha256 HASH`. Read errors, contradictions, exit status, and any required unread tail before concluding. A redacted or selected page is advisory evidence, never authorization or a replacement for executed verification. See [evidence behavior](EVIDENCE.md) for fallback and recovery details.

## Guard shell commands (optional)

Command Code runs `PreToolUse` shell hooks from `~/.commandcode/settings.json` or `.commandcode/settings.json` before `shell_command` executes. `jev hook config command-code` prints a fragment with matcher `^shell$` (Command Code matches case-insensitive regular expressions against display names, and `SHELL` is `shell_command`). Merge it into the existing `hooks` object and reload.

Command Code hooks can allow or deny but cannot ask. So in `bypass` and `dont-ask` sessions, where nobody reviews commands, the guard **denies** a command Jev flags as destructive or sensitive and gives the agent the reason. In other modes it stays silent, so your approval prompt keeps the decision. Add `--when always` to the hook command to deny flagged commands in every mode. The guard never approves anything, skips plain read-only commands without a request, and on any local failure produces no decision. `JEV_HOOK=off` disables it. Details and the other harnesses are in [HOOKS.md](HOOKS.md).

## Why saved-output reading uses a skill

The current [mod contract](https://commandcode.ai/docs/mods#hooks-and-events) exposes `afterToolCall`, which can replace the result before it is committed to model context. That is a plausible future adapter, but a generic tool result does not establish the immutable source, approved upload scope, matching workload identity, and qualified omission policy this runtime requires. Mods are unsandboxed trusted code and their API is experimental. This integration instead exposes an explicit saved-artifact workflow through existing tools; it adds no permission grants, automatic retries, or event hooks.

## Evidence checked on 2026-09-28 and 2026-09-30

On 2026-09-30 the published `command-code` 1.72.4 package (`dist/cli.mjs`) was inspected read-only for the hook runner. A `hookSpecificOutput.permissionDecision` of `deny` blocks the tool. Matchers compile with `new RegExp(pattern, "i")` against display names. Plan mode skips tool hooks. `shell_command` input is passed through unchanged as `command`, `args` and `cwd`. MCP locations, `${NAME:-}` expansion and `disable-model-invocation` are unchanged from 1.66.0.


The locally installed npm package reported **`command-code` 1.66.0** in its `package.json`. Read-only inspection of its bundled skill/MCP/mod references and `dist/cli.mjs` confirmed the manual-only skill field and `${NAME:-}` stdio environment expansion. The package's skill catalog and MCP paths match the current official documentation above.

- `package.json` SHA-256: `59ff1b0414e415611a6318f1f38c80aabeeb70aa50feb781d02a503531ae3f8a`.
- `dist/cli.mjs` SHA-256: `adf05d4e64358d631e4627e7cfaee23ac7903690ca44fe5b0e715f8b814b95ca`.

Automated checks use temporary profiles, synthetic capture subprocesses, and the local JSON CLI. They establish generated configuration, preservation/restore behavior, and an offline capture-to-evidence path. They do **not** establish a Command Code UI invocation, provider authentication, live mod behavior, or token/time savings. Record those separately for the actual client version and workload before advertising them.
