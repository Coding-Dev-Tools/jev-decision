# jev-decision

Zero-dependency Python client, typed primitives, calibration profiles, and agent guardrails for **Jev (TypeSafe AI)** System 1 decision engine.

Based on the architecture by Diogo Almeida (@CompleteSkeptic).

## Features

- **Zero Third-Party Dependencies**: Pure standard library (`urllib.request`, `json`, `math`). Works in any environment.
- **Typed Primitives**:
  - `Noul`: Calibrated binary probability $P(\text{True})$.
  - `Choice`: Categorical distribution over discrete options.
  - `Score`: Ordinal scale rating (e.g. 0 to 4).
- **Single Forward Pass Parallelism**: Evaluates multiple questions against shared state simultaneously.
- **Built-in Deterministic Fallback**: Works offline with zero network calls when unconfigured.
- **Turnkey Harness Guardrails**:
  - `guard_bash_command()`: Pre-execution safety and permission check.
  - `prune_tool_output()`: Log and diff context compression saving 80%+ tokens.
  - `verify_turn_completion()`: Verification stop and goal completion gating.
  - `classify_memory_relation()`: Memory contradiction and invalidation classifier.

## Quickstart

```python
from jev_decision import JevClient, guard_bash_command, prune_tool_output

# Initialize client (reads TYPESAFE_API_KEY from environment)
client = JevClient()

# Guard shell command execution
decision = guard_bash_command("git status", cwd="/repo", client=client)
if decision["allow_auto"]:
    # Execute immediately
    pass

# Prune bulky logs before sending to frontier LLM
compressed, stats = prune_tool_output(huge_log_str, current_goal="fix auth bug")
print(f"Saved {stats['saved_lines']} lines of context tokens!")
```
