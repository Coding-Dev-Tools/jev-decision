# Command Code: read saved output before loading it

The Command Code integration installs a focused, explicitly invoked `/jev-advice` skill and the `jev` MCP entry. It guides the agent to read approved saved logs through `jev_read_evidence` before those logs enter model context. Installation and `off` reads make no Jev provider requests. Shadow scoring and qualified selection require separate operator opt-in; no startup or post-tool hook runs inference automatically.

## Install and restore

Use an installed Python with `jev-decision[mcp]`; `-I` requires the package to be installed into that exact interpreter. Start with user scope and a zero budget. This keeps machine-specific interpreter/state paths in your own profile. Replace the three absolute paths with your own:

```powershell
$jevPython = 'C:/tools/jev/Scripts/python.exe'
$jevState = 'C:/Users/you/AppData/Local/JevDecision'
$projectRoot = 'C:/work/example'
& $jevPython -I -m jev_decision.cli --runtime-home $jevState setup --non-interactive --credential-source env --key-env TYPESAFE_API_KEY --workspace $projectRoot --daily-budget 0 --selection-mode off --harness command-code --scope user
& $jevPython -I -m jev_decision.cli --runtime-home $jevState harness install --target command-code --scope user --dry-run
& $jevPython -I -m jev_decision.cli --runtime-home $jevState harness install --target command-code --scope user --apply
```

On macOS/Linux, use `/absolute/venv/bin/python` and ordinary shell invocation without PowerShell's `&`. For a reviewed project integration, use `--scope project --project-root $projectRoot` on installation and restoration. Reconcile generated absolute paths before sharing project configuration with teammates.

Detection recognizes `command-code` on every platform, `cmdc` on native Windows and `cmd` on POSIX/WSL. Windows `cmd.exe` is never detected as Command Code. An executable on PATH establishes detection only; connection and actual invocation remain separate. [Command Code executable names](https://commandcode.ai/docs/windows#the-cmdc-alias)

| Scope | MCP entry | Managed skill |
| --- | --- | --- |
| User | `~/.commandcode/mcp.json` → `mcpServers.jev` | `~/.commandcode/skills/jev-advice/SKILL.md` |
| Project | `<project>/.mcp.json` → `mcpServers.jev` | `<project>/.commandcode/skills/jev-advice/SKILL.md` |

Command Code's private local scope can override project and user MCP entries. Project `.mcp.json` may also be consumed by another client, so review the preview before applying. The installer preserves unrelated entries and refuses to adopt an unmanaged `jev` entry. These locations and precedence are documented in [Command Code MCP](https://commandcode.ai/docs/mcp#configuration--scopes).

If the shared project's `jev` entry is already managed for Claude Code, Command Code installation/restoration reports `shared_client_ownership_conflict`; the reverse order is also protected. Use user scope for independent client configurations, or have the operator reconcile a shared entry. An external edit to an owned entry remains a conflict rather than being silently restored.

The generated entry binds an absolute interpreter and `JEV_HOME`. An environment credential uses `${TYPESAFE_API_KEY:-}` (or your selected variable name), never its value. The empty fallback allows credential-free off reads. Supply the real variable in the client launch environment only when enabling provider use, or choose the supported OS vault in `jev setup`. Use a dedicated credential name; `JEV_HOME`, `JEV_ENDPOINT_URL` and `JEV_OFFLINE_MODE` are reserved runtime variables. The installed skill's CLI command also binds the absolute runtime home.

To restore the user integration above, preview first, then apply:

```powershell
& $jevPython -I -m jev_decision.cli --runtime-home $jevState harness restore --target command-code --scope user --dry-run
& $jevPython -I -m jev_decision.cli --runtime-home $jevState harness restore --target command-code --scope user --apply
```

Restoration uses saved ownership records, retains unrelated edits, and reports conflicts instead of overwriting changed managed content. It does not erase the runtime, credentials, budget ledger, or captured evidence. For a project integration, replace `--scope user` with `--scope project --project-root $projectRoot` in both restore commands.

## Invoke the workflow

Reload the client, inspect `/mcp` for the `jev` connection, and inspect `/skills` for `jev-advice`. Complete any normal workspace trust or tool approval prompts. The skill sets `disable-model-invocation: true`; invoke it explicitly instead of expecting automatic discovery from the model's skill catalog. [Command Code skills](https://commandcode.ai/docs/skills#skills-specification)

If a built-in command or another skill has the same short name, use `/skill:jev-advice` to select the skill explicitly. Project skills take precedence over user skills; check `/skills` for the active source before using it. [Skill selection priority](https://commandcode.ai/docs/skills#selection-priority)

For an existing saved log, type a path as ordinary text, without attaching the entire file:

```text
/jev-advice C:/work/example/.jev-captures/run-001/stderr.log Explain the first failed test. Use off mode; do not rerun the producer.
```

The skill directs the agent to call `mcp__jev__jev_read_evidence` with a bounded page. The equivalent direct CLI call is:

```powershell
& $jevPython -I -m jev_decision.cli --runtime-home $jevState evidence --file 'C:/work/example/.jev-captures/run-001/stderr.log' --goal 'Explain the first failed test' --mode off --max-lines 200 --json
```

For a command that has not run, use the installed `capture` subcommand through the ordinary shell tool, under the same permissions as the producer. No repository checkout is needed. It executes a native argv command and saves stdout, stderr, exit status, sizes and hashes. This synthetic example demonstrates a failed producer while returning only a capture reference:

```powershell
& $jevPython -I -m jev_decision.cli capture --directory 'C:/work/example/.jev-captures/run-001' -- $jevPython -X utf8 -c 'import sys; print("collected 3 tests"); print("FAILED test_export", file=sys.stderr); sys.exit(7)'
```

Use a new capture directory for each run. Retain the helper's exit status `7` and `capture.json`; the helper does not reinterpret success. Read both saved streams when relevant. Never pipe the original log through the agent and then claim later scoring saved its context tokens. For output above the evidence reader's file limit, retain the original and produce bounded, provenance-preserving chunks before reading; a truncated excerpt is not a complete run.

On Windows, capture rejects `.cmd`/`.bat` wrappers, including wrappers resolved through PATH, because the operating system can introduce implicit command-shell parsing. Use the underlying executable, such as `node.exe --test` or `node.exe /absolute/tool-entry.js`. Running a batch file through an explicitly supplied interpreter requires the same operator authorization as that shell command. [Python subprocess security considerations](https://docs.python.org/3/library/subprocess.html#security-considerations)

Evidence pages require UTF-8. Capture preserves bytes even when a producer emits a different encoding. A CP1252/UTF-16/UTF-32 log gets an encoding-specific diagnostic. Configure the producer for UTF-8, or produce a separate UTF-8 derivative using its documented encoding; retain the original and hash, and use the derivative's own hash when reading it. Never replace undecodable bytes or claim a converted hash identifies the original artifact.

For a Command Code agent using Engraphis alongside Jev, recall authorized context through Engraphis first and keep its workspace/session routing. The [memory-system recipe](MEMORY_SYSTEMS.md) supplies bounded relationship, relevance and verification-gap advice through `jev_decide`; durable memory operations still use Engraphis' governed tools. Installing Jev preserves existing Engraphis MCP entries.

## Off, shadow, and qualified select

`off` is the initial policy. To run an explicitly authorized shadow trial, first choose a nonzero daily cap and save the mode. For example, the operator can repeat setup with `--daily-budget 0.25 --selection-mode shadow` after approving that cap and configuring credentials. A later evidence call may request `--mode shadow`; it retains the entire sanitized page while collecting scoring metadata. Per-call options cannot upgrade a saved `off` policy.

`select` requires a reviewed profile configured by the operator and [qualification evidence](EVALUATION.md). Pass an independently established workload identity with the real `harness`, `harness_version`, `primary_model`, and `primary_provider`; use MCP's `workload` object or CLI `--workload /absolute/workload.json`. Do not fill these fields by copying a profile. No Command Code selection profile ships, and no saved policy is upgraded by this skill.

Keep `source_sha256` and page metadata. Retrieve an omitted or later range with `--mode off --start-line N --max-lines M --expected-source-sha256 HASH`. Read errors, contradictions, exit status, and any required unread tail before concluding. A redacted or selected page is advisory evidence, never authorization or a replacement for executed verification. See [evidence behavior](EVIDENCE.md) for fallback and recovery details.

## Why this uses a skill

The current [mod contract](https://commandcode.ai/docs/mods#hooks-and-events) exposes `afterToolCall`, which can replace the result before it is committed to model context. That is a plausible future adapter, but a generic tool result does not establish the immutable source, approved upload scope, matching workload identity, and qualified omission policy this runtime requires. Mods are unsandboxed trusted code and their API is experimental. This integration instead exposes an explicit saved-artifact workflow through existing tools; it adds no permission grants, automatic retries, or event hooks.

## Evidence checked on 2026-09-28

The locally installed npm package reported **`command-code` 1.66.0** in its `package.json`. Read-only inspection of its bundled skill/MCP/mod references and `dist/cli.mjs` confirmed the manual-only skill field and `${NAME:-}` stdio environment expansion. The package's skill catalog and MCP paths match the current official documentation above.

- `package.json` SHA-256: `59ff1b0414e415611a6318f1f38c80aabeeb70aa50feb781d02a503531ae3f8a`.
- `dist/cli.mjs` SHA-256: `adf05d4e64358d631e4627e7cfaee23ac7903690ca44fe5b0e715f8b814b95ca`.

Automated checks use temporary profiles, synthetic capture subprocesses, and the local JSON CLI. They establish generated configuration, preservation/restore behavior, and an offline capture-to-evidence path. They do **not** establish a Command Code UI invocation, provider authentication, live mod behavior, or token/time savings. Record those separately for the actual client version and workload before advertising them.
