---
name: jev-decision
description: Use Jev (TypeSafe AI) System 1 decision engine for ultra-fast, zero-token-waste micro-decisions (bash safety checks, context pruning, loop verification, and memory contradiction resolution).
---

# Jev System 1 Decision Skill

This skill teaches agents how to leverage **Jev (TypeSafe AI)** for ultra-fast (70-300ms), zero-output-token decision gating, avoiding expensive System 2 generative LLM calls for micro-evaluations.

## Core Rules

1. **Never use a generative LLM for pure classification or safety gating**:
   - Use `jev_decision.guard_bash_command()` before running potentially mutating or dangerous terminal commands.
   - Use `jev_decision.prune_tool_output()` on bulky test logs or diffs before feeding them into prompt history.
   - Use `jev_decision.verify_turn_completion()` to confirm empirical verification before declaring victory.

2. **Understand the Primitives**:
   - `Noul`: Binary calibrated probability $P(\text{True}) \in [0.0, 1.0]$.
   - `Choice`: Categorical distribution over a discrete set of string options.
   - `Score`: Ordinal scale rating (e.g. 0 to 4).

3. **Calibrated Confidence Tiers**:
   - **High Stakes (Destructive/Secrets)**: Require $p \ge 0.95$ for autonomous execution. Otherwise pause and prompt user.
   - **Medium Stakes (Loop Completion/Contradiction)**: Require $p \ge 0.85$.
   - **Low Stakes (Pruning/Filtering)**: Retain items with $p \ge 0.40$ (fail-open to avoid dropping needed context).

4. **Multi-Question Parallel Pass**:
   - Always batch related questions together in a single `client.evaluate(state, questions)` call. Jev evaluates 10 questions in the same ~300ms forward pass as 1 question.

## Quick Python Usage

```python
from jev_decision import JevClient, guard_bash_command, prune_tool_output, verify_turn_completion

# 1. Shell Safety Guard
safety = guard_bash_command("git status", cwd="/repo")
if safety["allow_auto"]:
    # Safe to run without human confirmation
    pass

# 2. Context Pruning (Saves 80%+ tokens)
pruned_log, stats = prune_tool_output(huge_log_str, current_goal="fix auth endpoint")

# 3. Verification Gating
check = verify_turn_completion(
    goal="Fix issue #42",
    recent_actions="edited file.py and ran pytest",
    last_output="100% green, 45 passed in 0.2s"
)
if not check["is_complete"]:
    # Do not exit; run tests first
    pass
```

## Cross-Machine Setup

On any machine:
```bash
# Install via git
pip install git+https://github.com/Coding-Dev-Tools/jev-decision.git

# Set optional API key (offline fallback works automatically without it)
export TYPESAFE_API_KEY="your-key"
```
