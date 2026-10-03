# Pre-execution shell guard (`jev hook`)

A harness **pre-tool hook** can request Jev advice synchronously before a shell command runs, without requiring the primary model to choose the assessment tool. The optional adapter escalates flagged commands through the harness's own decision contract. Its quality, cost and latency require measurement on the intended workload.

## What it does

For each shell command the harness is about to run, `jev hook run HARNESS`:

1. Reads the hook payload on stdin and extracts the command and working directory.
2. Skips a conservative set of simple inspection commands (`ls`, `cat README.md`, `git status`, `git log`, ...). Path-qualified executables, unknown options, shell syntax, credential/private names, and paths outside the working directory trigger assessment. Options that write output or execute helpers, such as `sort -o` and `rg --pre`, also trigger assessment.
3. Otherwise asks Jev two questions in one budgeted request: a Choice over `inspection` / `test_or_build` / `mutation` / `destructive_or_sensitive` / `unclear`, each with an explicit meaning, and a Noul for material risk. Input size depends on the command and criteria; no fixed per-command cost or latency is guaranteed.
4. Flags the command when either probability of destructive or sensitive effects reaches the threshold (default `0.8`).

| Harness | Decision on a flagged command | When it acts |
| --- | --- | --- |
| Claude Code | `ask`: the normal approval prompt appears, even for allowlisted commands | Every session |
| Cursor | `ask` | Every session |
| Command Code | `deny` with a reason | Sessions without approval prompts (`bypass`, `dont-ask`) |
| Codex | `deny` with a reason | `bypassPermissions` / `dontAsk` sessions |
| Gemini CLI | `deny` with a reason | Payloads that report `yolo` mode; if your version omits the mode, use `--when always` |

This adapter uses `deny` for Command Code, Codex and Gemini CLI, whose documented pre-tool contracts do not support `ask`. By default it acts in these harnesses only in payloads that report unattended modes. Add `--when always` to block flagged commands in every mode.

**It never answers "allow".** Allowlists, permission modes, sandboxing and approval prompts behave exactly as before. The guard can only add a prompt or a block.

**It fails open.** A fresh install, a missing key, an exhausted budget, a timeout (3 s), a provider error, a malformed payload, or even a stale argument in the settings file all produce no decision, and the command goes through the harness's normal flow. The hook always exits `0`, because exit code `2` means "block" to several harnesses.

## Install

Complete `jev setup` first and configure a nonzero daily budget and a credential source. For an environment source, set its selected key variable in the harness launch environment. Exporting a key alone does not enable a fresh hook. Then print the fragment for your harness:

```sh
jev hook config claude-code      # or: command-code, codex, cursor, gemini-cli
```

The output names the settings files to merge into and a `fragment` with absolute paths to your interpreter and runtime home. Merge it into the existing `hooks` object. Don't replace other entries. Then reload the client.

Claude Code (`~/.claude/settings.json` or `.claude/settings.json`) uses exec form, so no shell quoting is involved:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash|PowerShell",
        "hooks": [
          {
            "type": "command",
            "command": "/home/you/.local/share/uv/tools/jev-decision/bin/python",
            "args": ["-I", "-m", "jev_decision.cli", "--runtime-home", "/home/you/.local/state/JevDecision", "hook", "run", "claude-code"],
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Command Code (`~/.commandcode/settings.json` or `.commandcode/settings.json`). Its matchers are case-insensitive regular expressions tested against display names; `^(shell|powershell)$` covers `shell_command` and the native Windows `powershell` tool:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^(shell|powershell)$",
        "hooks": [
          {
            "type": "command",
            "command": "/home/you/.local/share/uv/tools/jev-decision/bin/python -I -m jev_decision.cli --runtime-home /home/you/.local/state/JevDecision hook run command-code",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Codex uses `~/.codex/hooks.json` with the same `PreToolUse` / `Bash` shape. Cursor uses `~/.cursor/hooks.json` with `beforeShellExecution`. Gemini CLI uses `settings.json` → `hooks.BeforeTool` with matcher `run_shell_command`; its timeout is in milliseconds. `jev hook config` prints each of these.

## Tune or disable

| Setting | Effect |
| --- | --- |
| `JEV_HOOK=off` | Disable without editing settings (in the harness launch environment) |
| `JEV_HOOK_THRESHOLD=0.9` | Flag less often. `--threshold` in the hook command does the same. |
| `--when always` | Deny-only harnesses: block flagged commands in every permission mode |

The default threshold is an engineering choice, not a calibrated value. Before relying on it, run your own command history through `jev guard` and pick a threshold from the outcomes you observe.

## Evidence status

The payload and output contracts come from each vendor's hook documentation as of 2026-09-30. For Command Code they were also checked against the hook runner shipped in `command-code` 1.72.4 (`dist/cli.mjs`): the `hookSpecificOutput.permissionDecision` of `deny` blocks, matchers compile as case-insensitive `RegExp` over display names (`SHELL` for `shell_command`), and `tool_input` carries `command`, `args` and `cwd`. Automated tests cover payload parsing, decisions, fail-open behavior and the generated fragments for all five harnesses. They do not launch any of these clients, so treat each harness as unverified live until you have watched a flagged command prompt or block in your own version.

Sources: [Claude Code hooks](https://code.claude.com/docs/en/hooks), [Command Code hooks](https://commandcode.ai/docs/hooks), [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Cursor hooks](https://cursor.com/docs/agent/hooks), [Gemini CLI hooks](https://geminicli.com/docs/hooks/reference).
