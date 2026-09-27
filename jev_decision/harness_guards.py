"""High-level harness guardrails and context pruning utilities powered by Jev.

Can be imported directly into agent loops (Hermes, CLI agents) and Engraphis.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .client import JevClient
from .primitives import (
    CalibrationTier,
    ChoiceQuestion,
    DEFAULT_CALIBRATION,
    NoulQuestion,
    ScoreQuestion,
)


def guard_bash_command(
    command: str,
    *,
    cwd: str = "",
    client: Optional[JevClient] = None,
    calibration: CalibrationTier = DEFAULT_CALIBRATION,
) -> Dict[str, Any]:
    """Evaluate whether a bash/shell command is safe for autonomous execution.

    Returns:
        Dict with keys:
            - allow_auto: bool (True if safe to run without user prompt)
            - category: str ('read_only', 'compile_test', 'git_inspection', 'file_mutation', 'destructive_or_leak')
            - safety_probability: float
            - escalate_to_user: bool
    """
    c = client or JevClient()
    state = f"CWD: {cwd}\nCOMMAND: {command.strip()}"

    questions = [
        NoulQuestion(
            id="is_safe",
            prompt="Is this command completely safe to run autonomously without data loss, system damage, or secret leaks?",
        ),
        ChoiceQuestion(
            id="category",
            prompt="Categorize the primary nature of this command",
            options=[
                "read_only",
                "compile_test",
                "git_inspection",
                "file_mutation",
                "destructive_or_leak",
            ],
        ),
    ]

    batch = c.evaluate(state, questions)
    safe_dec = batch.get_noul("is_safe")
    cat_dec = batch.get_choice("category")

    prob = safe_dec.probability if safe_dec else 0.5
    cat = cat_dec.selected if cat_dec else "unknown"

    # Strict tier for destructive actions
    allow_auto = prob >= calibration.tier_destructive and cat != "destructive_or_leak"
    escalate = not allow_auto

    return {
        "allow_auto": allow_auto,
        "escalate_to_user": escalate,
        "category": cat,
        "safety_probability": prob,
        "latency_ms": batch.latency_ms,
        "is_fallback": batch.is_fallback,
    }


def prune_tool_output(
    raw_output: str,
    current_goal: str,
    *,
    client: Optional[JevClient] = None,
    max_retained_lines: int = 100,
    calibration: CalibrationTier = DEFAULT_CALIBRATION,
) -> Tuple[str, Dict[str, Any]]:
    """Prune bulky tool outputs (e.g. 5,000 lines of logs or diffs) to save context tokens.

    Splits the output into logical chunks, evaluates relevance to current_goal,
    and replaces non-relevant blocks with concise omission markers.
    """
    lines = raw_output.splitlines()
    if len(lines) <= max_retained_lines:
        return raw_output, {"pruned": False, "saved_lines": 0}

    c = client or JevClient()

    # Chunk into 25-line slices
    chunk_size = 25
    chunks: List[Tuple[int, int, str]] = []
    for i in range(0, len(lines), chunk_size):
        chunk_text = "\n".join(lines[i : i + chunk_size])
        chunks.append((i, min(i + chunk_size, len(lines)), chunk_text))

    retained_slices: List[str] = []
    saved_lines = 0
    total_chunks = len(chunks)

    # For fast gating: evaluate first, last, and middle chunks
    for start_idx, end_idx, chunk_text in chunks:
        # Fast local heuristic check: stack traces or error markers are always retained
        if any(err in chunk_text.lower() for err in ["error", "fail", "exception", "traceback"]):
            retained_slices.append(chunk_text)
            continue

        q = ScoreQuestion(
            id=f"rel_{start_idx}",
            prompt=f"How relevant is this terminal chunk to the debugging/development goal: '{current_goal}'?",
            scale=[0, 1, 2, 3, 4],
        )
        batch = c.evaluate(f"GOAL: {current_goal}\nCHUNK:\n{chunk_text[:1000]}", [q])
        score_dec = batch.get_score(f"rel_{start_idx}")
        score_val = int(score_dec.score) if score_dec and isinstance(score_dec.score, (int, float)) else 2

        if score_val >= 2:
            retained_slices.append(chunk_text)
        else:
            omitted = end_idx - start_idx
            saved_lines += omitted
            retained_slices.append(f"[... {omitted} lines of boilerplate/passing output omitted by Jev ...]")

    pruned_output = "\n".join(retained_slices)
    return pruned_output, {
        "pruned": True,
        "original_lines": len(lines),
        "saved_lines": saved_lines,
        "token_savings_est": saved_lines * 12,
    }


def verify_turn_completion(
    goal: str,
    recent_actions: str,
    last_output: str,
    *,
    client: Optional[JevClient] = None,
    calibration: CalibrationTier = DEFAULT_CALIBRATION,
) -> Dict[str, Any]:
    """Verify whether an agent turn genuinely completed its goal or requires test/build proof.

    Returns:
        Dict with keys:
            - is_complete: bool
            - completion_probability: float
            - needs_verification_run: bool
    """
    c = client or JevClient()
    state = f"GOAL: {goal}\nRECENT ACTIONS: {recent_actions}\nLAST OUTPUT: {last_output}"

    questions = [
        NoulQuestion(
            id="is_complete",
            prompt="Based on the recent actions and test output, is the stated goal genuinely and fully completed?",
        ),
        NoulQuestion(
            id="unverified_edits",
            prompt="Were source code changes made without running a compilation or test check to verify them?",
        ),
    ]

    batch = c.evaluate(state, questions)
    comp_dec = batch.get_noul("is_complete")
    unv_dec = batch.get_noul("unverified_edits")

    comp_prob = comp_dec.probability if comp_dec else 0.5
    unv_prob = unv_dec.probability if unv_dec else 0.5

    is_complete = comp_prob >= calibration.tier_loop_halt and unv_prob < 0.30
    needs_verify = unv_prob >= 0.50

    return {
        "is_complete": is_complete,
        "completion_probability": comp_prob,
        "needs_verification_run": needs_verify,
        "latency_ms": batch.latency_ms,
    }


def classify_memory_relation(
    new_fact: str,
    existing_memory: str,
    *,
    client: Optional[JevClient] = None,
) -> str:
    """Classify the semantic relationship between a new fact and an existing memory.

    Returns:
        "contradicts_and_supersedes" | "reinforces" | "orthogonal"
    """
    c = client or JevClient()
    state = f"EXISTING MEMORY: {existing_memory}\nNEW FACT: {new_fact}"

    q = ChoiceQuestion(
        id="relation",
        prompt="Determine the semantic relationship of the NEW FACT with the EXISTING MEMORY",
        options=["contradicts_and_supersedes", "reinforces", "orthogonal"],
    )

    batch = c.evaluate(state, [q])
    dec = batch.get_choice("relation")
    return dec.selected if dec else "orthogonal"
