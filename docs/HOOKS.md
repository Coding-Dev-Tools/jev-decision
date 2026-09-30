# Pre-execution shell guard (`jev hook`)

Coding agents run many shell commands, and a few of them delete data, rewrite shared history, or leak secrets. A generative model is too slow and too expensive to review every command. Jev answers in one short forward pass, and a harness **pre-tool hook** can call it synchronously, before the command runs, without the primary model spending tokens or remembering to ask. This is the integration pattern TypeSafe and LangChain recommend: [check tool calls for risky decisions and block them before the tool executes](https://www.langchain.com/blog/building-a-harness-with-jev).

## What it does

For each shell command the harness is about to run, `jev hook run HARNESS`:

1. Reads the hook payload on stdin and extracts the command and working directory.
2. Skips simple read-only commands (`ls`, `cat README.md`, `git status`, `git log`, `grep`, ...). A command is skipped only when it has no shell syntax (pipes, `;`, `&&`, redirects, substitution, globs) and no argument that names a credential or private file.
3. Otherwise asks Jev two questions in one request: a Choice over `inspection` / `test_or_build` / `mutation` / `destructive_or_sensitive` / `unclear`, each with an explicit meaning, and a Noul for material risk. That is about 100 input tokens, or roughly $0.000004 per command.
4. Flags the command when either probability of destructive or sensitive effects reaches the threshold (default `0.8`).

| Harness | Decision on a flagged command | When it acts |
| --- | --- | --- |
| Claude Code | `ask`: the normal approval prompt appears, even for allowlisted commands | Every session |
| Cursor | `ask` | Every session |
| Command Code | `deny` with a reason | Sessions without approval prompts (`bypass`, `dont-ask`) |
| Codex | `deny` with a reason | `bypassPermissions` / `dontAsk` sessions |
| Gemini CLI | `deny` with a reason | Payloads that report `yolo` mode; if your version omits the mode, use `--when always` |

Command Code, Codex and Gemini CLI hooks can only allow or deny. Denying in a session where a person would have approved the command anyway would take that choice away from them, so by default the guard acts in these harnesses only when nobody is reviewing commands. Add `--when always` to the hook command to block flagged commands in every mode.

**It never answers "allow".** Allowlists, permission modes, sandboxing and approval prompts behave exactly as before. The guard can only add a prompt or a block.

**It fails open.** A fresh install, a missing key, an exhausted budget, a timeout (3 s), a provider error, a malformed payload, or even a stale argument in the settings file all produce no decision, and the command goes through the harness's normal flow. The hook always exits `0`, because exit code `2` means "block" to several harnesses.

## Install

Complete `jev setup` first, or export `TYPESAFE_API_KEY` in the harness's environment. Then print the fragment for your harness:

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

Command Code (`~/.commandcode/settings.json` or `.commandcode/settings.json`). Its matchers are case-insensitive regular expressions tested against display names, and `^shell$` matches only `shell_command`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^shell$",
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
