# Integration recipes and support evidence

This matrix describes the 0.3 interfaces, not universal live support. A passing configuration fixture verifies owned-file edits and restoration. A protocol subprocess verifies the server adapter. **Only a recorded invocation through the named client/version verifies that client.** Provider authentication is a further, separate check; workload savings require the evaluation gate.

## Choose a target

After `jev setup`, run `jev harness install --target TARGET --scope user --dry-run`, then repeat with `--apply`. All recipes use an absolute installed Python, `-I -m jev_decision.mcp`, and the selected `JEV_HOME`. Project scope also requires `--project-root /absolute/project`.

| Target | User configuration | Project configuration | Evidence for this implementation |
| --- | --- | --- | --- |
| `codex` | `$CODEX_HOME/config.toml` or `~/.codex/config.toml` | `.codex/config.toml` in a trusted project | TOML install/restore fixtures; live CLI/Desktop separately unverified |
| `claude-code` | `~/.claude.json` | `.mcp.json` | JSON install/restore fixtures; actual client/version unverified |
| `claude-desktop` | Windows `%APPDATA%/Claude/claude_desktop_config.json`; macOS `~/Library/Application Support/Claude/claude_desktop_config.json` | Not offered | OS path fixtures; actual client/version unverified; Linux recipe unavailable |
| `cursor` | `~/.cursor/mcp.json` | `.cursor/mcp.json` | JSON install/restore fixtures; actual client/version unverified |
| `gemini-cli` | `~/.gemini/settings.json` | `.gemini/settings.json` | Native MCP plus env-reference fixtures; actual client/version unverified |
| `antigravity`, `antigravity-ide` | `~/.gemini/config/mcp_config.json` | `.agents/mcp_config.json` | Shared-path ownership fixtures; CLI and IDE require separate live verification |
| `opencode` | `$OPENCODE_CONFIG` or `~/.config/opencode/opencode.json[c]` | `opencode.json[c]` | JSONC install/restore fixtures; actual client/version unverified |
| `command-code` | `~/.commandcode/mcp.json` | `.mcp.json`; skill under `.commandcode/skills` | Manual skill and install/restore fixtures; installed 1.66.0 source checked; actual client invocation unverified |
| `crush` | Configured Crush global config/data location | Not offered | Preserved native adapter; current package/client pair unverified |
| `pi`, `hermes`, `omp`, `openclaude`, `copilot` | Respective user skill directories | Not offered | CLI skill rendering/restore fixtures; client skill discovery unverified |
| Generic MCP | Client-defined stdio configuration | Client-defined | Official SDK 2.2 real subprocess: legacy, auto, and `2026-07-28`; UTF-8 Windows pipes tested |
| Generic shell/tool harness | JSON CLI `jev decide` / `jev evidence` | Caller chooses directory | Actual subprocess contract tests; no particular agent client implied |

Path references: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), [Claude Code MCP](https://code.claude.com/docs/en/mcp), [Claude Desktop local servers](https://modelcontextprotocol.io/docs/develop/connect-local-servers), [Cursor MCP](https://cursor.com/docs/mcp), [Gemini MCP](https://geminicli.com/docs/tools/mcp-server/), [Antigravity MCP](https://antigravity.google/docs/mcp), [OpenCode MCP](https://opencode.ai/docs/mcp-servers/), [Command Code MCP](https://commandcode.ai/docs/mcp#configuration--scopes). Paths and client behavior can change; record versions when verifying a deployment.

The [Command Code guide](COMMAND_CODE.md) covers the explicit `/jev-advice` skill, capture before ingestion, off/shadow/qualified-select use, and recovery. Command Code and Claude Code can share a project `.mcp.json`; the installer refuses to transfer ownership of one client's managed `jev` entry to the other. Use user scope for independent configurations.

Targets that lack a detected executable report that fact. Creating an entry or discovering a profile directory does not prove the client can start it. Project trust, managed policy, plugins, and settings precedence can affect discovery. The runtime never changes those policies.

## Generic MCP

Merge the `jev` entry into your client's supported stdio configuration, using absolute paths:

```json
{
  "mcpServers": {
    "jev": {
      "command": "/absolute/venv/bin/python",
      "args": ["-I", "-m", "jev_decision.mcp"],
      "env": {"JEV_HOME": "/absolute/shared-jev-state"}
    }
  }
}
```

Use `C:/absolute/venv/Scripts/python.exe` on Windows. Install `jev-decision[mcp]` into that exact interpreter. The adapter lazily imports the [official SDK](https://py.sdk.modelcontextprotocol.io/) and preserves `jev-mcp`, `jev mcp`, module execution, and all six tool names. It has typed input/output schemas. Core Python 3.9 imports do not require the SDK.

Codex uses `[mcp_servers.jev]` in TOML; OpenCode uses `mcp.jev` with `type: "local"`, an argv `command` array and `environment`. The selected installer renders those native formats. Gemini and Claude Code expand `${NAME}` references; Cursor uses `${env:NAME}`. Command Code uses `${NAME:-}` so a missing key permits off reads. These entries contain variable names, never key values. OpenCode inherits its launch environment. For other clients, use an OS vault or verify how that exact client passes an environment variable. GUI launches may not inherit a terminal's environment.

## Generic JSON CLI

Send UTF-8 JSON to `jev decide --file -`, or save it and pass `--file path`. The response is JSON with explicit `status`, `source`, typed decisions, model identity, usage, attempts and error code. Exit code 2 denotes unavailable/invalid input; no synthetic prediction replaces it. `off` evidence reads return a successful artifact page even if provider credentials are absent.

An installed skill embeds an absolute command such as:

```sh
/absolute/venv/bin/python -I -m jev_decision.cli --runtime-home /absolute/shared-jev-state decide --file request.json
```

This keeps a shell-only harness on the same credential and budget policy as native MCP clients. Python and TypeScript direct-library integrations remain available for custom adapters. The TypeScript SDK does not enforce this shared ledger; applications needing it should invoke the Python CLI/MCP.

## Verify and record each stage

1. **Configuration:** preview/apply output shows only selected Jev-owned entries. Retain ownership backups for selective restore.
2. **Connection:** reload the named client and inspect its actual tool list. Record executable path, client version, OS and Jev package hash.
3. **Invocation:** ask that client to invoke `jev_status`. Record the real tool event; a successful shell call alone is not a desktop invocation.
4. **Authentication:** when a live request is authorized, invoke a small synthetic `jev_decide` through that client. Require `status=ok`, `source=provider`, the pinned resolved model and the matching tool event. A cache hit is not fresh authentication.
5. **Benefit:** use independently labeled, matched evaluation. Configuration and authentication do not establish savings.

Store content-free metadata or sanitized traces and their hashes. Recheck after client, runtime, model or profile changes. Hosted ChatGPT does not inherit local stdio entries; a private authenticated remote bridge requires its own deployment and client verification and is outside this release.
