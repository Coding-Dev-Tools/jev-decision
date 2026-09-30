# Jev Decision

[![CI](https://github.com/Coding-Dev-Tools/jev-decision/actions/workflows/ci.yml/badge.svg)](https://github.com/Coding-Dev-Tools/jev-decision/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Fast, typed, budgeted [TypeSafe Jev](https://docs.typesafe.ai/api) decisions for coding-agent harnesses: Command Code, Claude Code, Codex, Cursor, Gemini CLI, Antigravity, OpenCode, and any MCP or shell-capable client.

Jev is a "System 1" model: instead of generating text, it returns calibrated probabilities for yes/no (Noul), multiple-choice (Choice), and rubric (Score) questions in one forward pass. TypeSafe quotes 70–500 ms per request, $0.042 per million input tokens and free output tokens ([announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev)). That makes it a good fit for the small judgments an agent loop makes constantly, where calling the primary LLM would be slow and expensive.

**Advice, never authority.** Jev results never grant a permission, approve a command, or certify that a task is complete. Your harness's permission rules and your executed tests stay in charge. Any failure (no key, budget reached, timeout, provider error) returns an explicit `unavailable` result, and your workflow carries on as if Jev were absent.

## Quickstart

### 1. Install

MCP support needs Python 3.10+; the core library and CLI run on 3.9+.

```sh
# Recommended: an isolated tool install that puts `jev` and `jev-mcp` on PATH
uv tool install "jev-decision[mcp,setup] @ git+https://github.com/Coding-Dev-Tools/jev-decision"

# Or into a virtual environment you manage
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
python -m pip install "jev-decision[mcp,setup] @ git+https://github.com/Coding-Dev-Tools/jev-decision"
```

`setup` adds OS credential storage and timezone data. Generated harness entries point at this exact interpreter, so keep the environment in place after installing.

### 2. Configure a key and a daily budget

A fresh install makes **no** provider calls until you run setup.

```sh
jev setup            # interactive: key source, daily budget, workspace, harness
jev doctor --live    # one tiny budgeted request that checks authentication
```

`jev setup` stores the key in Windows DPAPI or the macOS/Linux keychain. It never writes the key to a config file. On headless machines, point setup at an environment variable *name* instead:

```sh
export TYPESAFE_API_KEY=...   # set this in the harness's launch environment
jev setup --non-interactive --credential-source env --daily-budget 1 --timezone UTC --workspace "$PWD"
```

A daily budget of `0` keeps requests disabled. Before each request the shared ledger reserves the worst-case cost of that request, so the cap holds across every harness that uses the same runtime.

### 3. Connect your harness

Preview the change, apply it, then reload the client:

```sh
jev harness install --target command-code --dry-run
jev harness install --target command-code --apply
```

| Harness | `--target` | What gets installed |
| --- | --- | --- |
| Command Code | `command-code` | `jev` MCP server plus the `/jev-advice` skill ([guide](docs/COMMAND_CODE.md)) |
| Claude Code | `claude-code` | MCP server and skill |
| Codex | `codex` | `[mcp_servers.jev]` and skill |
| Cursor | `cursor` | MCP server and skill |
| Gemini CLI | `gemini-cli` | MCP server and skill |
| Antigravity CLI / IDE | `antigravity`, `antigravity-ide` | MCP server and skill |
| OpenCode | `opencode` | MCP server and skill |
| Claude Desktop, Crush | `claude-desktop`, `crush` | MCP server |
| Pi, Hermes, OMP, OpenClaude, Copilot | `pi`, `hermes`, `omp`, `openclaude`, `copilot` | CLI-based skill |

Add `--scope project --project-root /abs/project` for a project-level configuration where the harness supports one. `jev harness restore --target NAME --apply` removes only what Jev added and reports any entries you changed yourself. File locations and verification status are in the [integration matrix](docs/INTEGRATIONS.md).

### 4. Optional: guard shell commands before they run

The most effective place for a ~100 ms check is the harness's own pre-tool hook, not a tool the primary model has to remember to call. `jev hook` classifies each shell command before it runs:

```sh
jev hook config claude-code     # prints the exact JSON to merge into your settings
```

The hook is **escalate-only**. It can force the harness's normal approval prompt (Claude Code, Cursor), or block a flagged command with a reason (Command Code, Codex, Gemini CLI). By default it blocks only in sessions that run without approval prompts. It never approves anything. Simple read-only commands such as `ls` or `git status` skip the request. Any local failure leaves the harness unchanged, and `JEV_HOOK=off` turns the hook off. See [docs/HOOKS.md](docs/HOOKS.md).

## Use it from code

```python
from jev_decision import JevClient

client = JevClient()  # saved `jev setup` runtime, or JevClient(api_key=...) / TYPESAFE_API_KEY before setup
batch = client.evaluate(
    {"request": "Export needs a preview before downloading."},
    [{"id": "intent", "type": "choice", "instructions": "Classify the requested change.",
      "criteria": {"feature": "Adds new behavior", "bug": "Fixes broken existing behavior", "unclear": None}}],
)
if batch.status == "ok":
    print(batch.get_choice("intent").selected, batch.get_choice("intent").probabilities)
else:
    print(batch.status, batch.error_code)   # carry on without Jev
```

The native ID-keyed map (`{"intent": {"type": "choice", ...}}`) and the typed `NoulQuestion`/`ChoiceQuestion`/`ScoreQuestion` classes also work. Ready-made helpers are `guard_bash_command`, `verify_turn_completion`, `classify_memory_relation`, and `prune_tool_output`. Passing a key to a library client before any `jev setup` counts as opting in, with the default $1/day cap on the shared ledger. The CLI and MCP server stay offline until setup.

From a shell or any harness without MCP, run `jev decide --file request.json` (see [examples](examples/)). TypeScript users have an explicit-key client in [`ts/`](ts/README.md). It uses the same wire contract, but the Python budget ledger does not cover its calls.

### MCP tools

| Tool | Purpose |
| --- | --- |
| `jev_decide` | Batch of typed Noul/Choice/Score questions over one state |
| `jev_guard_command` | Risk category and probability for a shell command (advisory) |
| `jev_verify_completion` | Gaps between a goal and the supplied verification evidence |
| `jev_read_evidence` | Read a saved log page by page, with source hashes and line references |
| `jev_prune_output` | Relevance measurement for text already in context |
| `jev_status` | Local configuration and budget, with no network call |

### Getting good answers

Follow the provider's [Jev 1.13 guidance](https://docs.typesafe.ai/model-jaggedness/jev-1.13):

- **Batch** related questions into one call. Extra questions add little latency.
- **Describe every option.** Give Choice labels and Score levels explicit meanings and boundary conditions.
- **Send only the relevant state.** Unrelated text acts as a distractor.
- **Keep deterministic work in code:** arithmetic, counting, date comparisons, parsing, and exit codes.
- **Calibrate thresholds for each question** on your own data. Don't reuse one question's threshold for another.

## Evidence capture and selection

A large log only saves context tokens if it never enters the model's context. `jev capture --directory /abs/new-dir -- pytest` runs the command and saves stdout, stderr, hashes, and the exit status. It prints only a small reference. `jev evidence` / `jev_read_evidence` then pages through the saved file inside approved workspace roots, with secrets redacted and original line numbers preserved.

Evidence **selection** (dropping low-relevance log spans) is off by default. **No workload ships qualified for automatic omission.** `shadow` mode measures, and `select` requires a locally qualified profile built with the [evaluation workflow](docs/EVALUATION.md). See [docs/EVIDENCE.md](docs/EVIDENCE.md). Savings depend on your logs, model, and harness, so this project makes no universal savings claim.

## Safety and accounting

- The model (`jev-1.13.0`) and the official HTTPS endpoint are pinned. There is no proxy discovery and no redirect following.
- Each attempt reserves its worst-case cost in a SQLite ledger shared by every process with the same `JEV_HOME`. There is at most one transient retry within a single deadline.
- Keys live in DPAPI, the OS keychain, or an environment variable you name. Harness configs contain only variable references. Unexpanded `${NAME}` placeholders are treated as missing keys.
- Question IDs, labels, and state are scanned for recognizable secrets and redacted before they leave the machine.

## Development

```sh
python -m pip install -e ".[test,mcp,setup]" build
python -m pytest -q
python scripts/check_packages.py --output /abs/new-dir     # wheel/sdist installed outside the checkout
cd ts && npm ci && npm test && npm run check:package
```

CI runs Python 3.9–3.14 on Windows, macOS and Linux, plus Node 22 and 24. [Migration from 0.2](docs/MIGRATION_0_3.md) · [Runtime contract](docs/SPECIFICATION.md) · [Validation records](docs/validation/README.md) · [MIT license](LICENSE)
