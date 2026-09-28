# jev-decision

[![CI](https://github.com/Coding-Dev-Tools/jev-decision/actions/workflows/ci.yml/badge.svg)](https://github.com/Coding-Dev-Tools/jev-decision/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python: >=3.9](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)

Zero-dependency System 1 decision engine, calibrated guardrails, MCP server, and token optimization client for **Jev (TypeSafe AI)**.

Based on the architecture by Diogo Almeida (@CompleteSkeptic) and validated against empirical benchmarks in *arXiv:2609.29429*.

---

## Key Features

- **Zero External Dependencies**: Pure standard library (`urllib.request`, `json`, `math`). Runs anywhere with zero pip bloat.
- **Universal Multi-Harness Support**:
  - **Python**: Drop-in client + guardrail helpers.
  - **Model Context Protocol (MCP)**: Native stdio MCP server for Cursor, Claude Desktop, Antigravity, Windsurf, Cline.
  - **TypeScript / Node.js**: Zero-dependency TS package under `ts/`.
  - **CLI**: Fast terminal inspection command (`jev guard`, `jev prune`, `jev verify`).
- **Disruptive Token & Latency Economics**:
  - 70–300ms single forward-pass latency.
  - $0.042 per million input tokens, **$0 output tokens**.
  - Upstream context pruning saving **80%–92%** of ongoing LLM prompt tokens.
- **Calibrated Probabilities (RLCD)**:
  - Strict high-stakes threshold ($p \ge 0.95$ for destructive commands).
  - Balanced medium-stakes threshold ($p \ge 0.85$ for task verification).
  - Permissive low-stakes threshold ($p \ge 0.40$ for context pruning).
- **Built-in Deterministic Offline Fallback**: Operates 100% offline with zero network calls when no API key is provided.

---

## Installation

### Python
```bash
pip install git+https://github.com/Coding-Dev-Tools/jev-decision.git
```
*(Or clone locally and run `pip install -e .`)*

### TypeScript / Node.js
```bash
cd ts && npm install && npm run build
```

---

## MCP Server Setup (Cursor / Claude Desktop / Antigravity)

Add to your `claude_desktop_config.json`, `antigravity.json`, or `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "jev-decision": {
      "command": "jev-mcp",
      "env": {
        "TYPESAFE_API_KEY": "your-api-key"
      }
    }
  }
}
```

Exposes four high-speed tools to your agent:
1. `jev_guard_command(command, cwd)`: Evaluates shell command safety in ~100ms.
2. `jev_prune_output(raw_output, current_goal)`: Prunes verbose boilerplate from command outputs.
3. `jev_verify_completion(goal, recent_actions, last_output)`: Verifies test proof before task completion.
4. `jev_decide(state, questions)`: Arbitrary parallel evaluations.

---

## CLI Usage

```bash
# Evaluate bash command safety
jev guard "git status"
# [ALLOWED (Auto-Execute)] Category: read_only | Safety: 0.98

jev guard "rm -rf / --no-preserve-root"
# [BLOCKED (Requires Approval)] Category: destructive_or_leak | Safety: 0.01

# Token-prune large build or test logs
pytest | jev prune --goal "fix authentication bug" --stats
```

---

## Python API Usage

```python
from jev_decision import JevClient, guard_bash_command, prune_tool_output, verify_turn_completion

client = JevClient()

# 1. Shell Safety Guard
safety = guard_bash_command("git status", cwd="/repo", client=client)
if safety["allow_auto"]:
    # Execute immediately without prompting user
    pass

# 2. Context Pruning
pruned, stats = prune_tool_output(huge_log, current_goal="fix auth endpoint", client=client)
print(f"Omitted {stats['saved_lines']} lines of boilerplate tokens!")

# 3. Task Completion Verification
check = verify_turn_completion(
    goal="Fix issue #42",
    recent_actions="edited file.py and ran pytest",
    last_output="100% green, 45 passed in 0.2s",
    client=client
)
if check["is_complete"]:
    # Safe to conclude session
    pass
```

---

## Agent Skill

To register the Jev skill with Claude Code or Antigravity:
```bash
# Claude Code:
npx skills add Coding-Dev-Tools/jev-decision

# Antigravity / Gemini:
cp SKILL.md ~/.gemini/antigravity/skills/jev-decision/SKILL.md
```

---

## License

MIT © Coding-Dev-Tools
