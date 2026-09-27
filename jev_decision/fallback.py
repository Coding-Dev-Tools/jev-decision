"""Deterministic heuristic fallbacks for Jev decisions when offline or API key is absent.

Zero third-party dependencies (pure standard library).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Union

from .primitives import (
    ChoiceDecision,
    ChoiceQuestion,
    Decision,
    DecisionBatch,
    NoulDecision,
    NoulQuestion,
    Question,
    ScoreDecision,
    ScoreQuestion,
)

# Known safe shell commands (read-only, inspection, testing)
SAFE_SHELL_PATTERNS = [
    re.compile(r"(?:^|\n|COMMAND:\s*)git\s+(status|diff|log|show|branch|rev-parse|stash\s+list)", re.IGNORECASE),
    re.compile(r"(?:^|\n|COMMAND:\s*)(ls|dir|cat|type|head|tail|grep|findstr|echo|pwd|where|which)\b", re.IGNORECASE),
    re.compile(r"(?:^|\n|COMMAND:\s*)(pytest|python\s+-m\s+pytest|npm\s+test|cargo\s+check|ruff\s+check)\b", re.IGNORECASE),
    re.compile(r"(?:^|\n|COMMAND:\s*)python\s+scripts/(check|test|lint)", re.IGNORECASE),
]

# Obvious high-risk destructive shell commands
DESTRUCTIVE_SHELL_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+[/~]", re.IGNORECASE),
    re.compile(r"\b(format|mkfs|fdisk|dd\s+if=)\b", re.IGNORECASE),
    re.compile(r"\b(drop\s+database|truncate\s+table)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+push\s+.*(--force|-f)\b", re.IGNORECASE),
    re.compile(r"\b(curl|wget)\b.*\|\s*(sh|bash|powershell|cmd)\b", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:", re.IGNORECASE),  # fork bomb
]

# Obvious polarity/negation markers
NEGATION_WORDS = frozenset(
    {"not", "no", "never", "none", "don't", "dont", "cannot", "cant", "deprecated", "removed", "disabled", "abandoned"}
)

STOP_WORDS = frozenset(
    {"the", "a", "an", "is", "in", "it", "to", "for", "of", "and", "or", "as", "at", "by", "this", "that", "how", "what", "which", "goal", "chunk", "command", "cwd", "relevant", "prompt", "question"}
)


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if w not in STOP_WORDS and len(w) > 1}


def heuristic_noul(question: NoulQuestion, state: str) -> NoulDecision:
    prompt_lower = question.prompt.lower()
    state_lower = state.lower()

    # Safety question
    if "safe" in prompt_lower or "destructive" in prompt_lower:
        for pat in DESTRUCTIVE_SHELL_PATTERNS:
            if pat.search(state):
                return NoulDecision(id=question.id, probability=0.01, confidence=0.99)
        for pat in SAFE_SHELL_PATTERNS:
            if pat.search(state):
                return NoulDecision(id=question.id, probability=0.98, confidence=0.95)
        # Default ambiguous safety
        return NoulDecision(id=question.id, probability=0.50, confidence=0.50)

    # Unverified edits question
    if "unverified" in prompt_lower or "without running" in prompt_lower:
        # If tests/checks were run successfully, edits are not unverified
        if any(ok in state_lower for ok in ["passed", "100% green", "success", "all checks passed"]):
            return NoulDecision(id=question.id, probability=0.05, confidence=0.90)
        if any(act in state_lower for act in ["edited", "modified", "patch", "write_file"]):
            return NoulDecision(id=question.id, probability=0.85, confidence=0.85)
        return NoulDecision(id=question.id, probability=0.20, confidence=0.70)

    # Verification / completion question
    if "complete" in prompt_lower or "finished" in prompt_lower:
        # If there are error traces in state, not complete
        if any(err in state_lower for err in ["error:", "failed", "traceback", "syntaxerror", "assertionerror"]):
            return NoulDecision(id=question.id, probability=0.05, confidence=0.95)
        if any(ok in state_lower for ok in ["passed", "100% green", "success", "all checks passed"]):
            return NoulDecision(id=question.id, probability=0.95, confidence=0.90)
        return NoulDecision(id=question.id, probability=0.60, confidence=0.60)

    # Contradiction question
    if "contradict" in prompt_lower or "supersede" in prompt_lower:
        tokens_s = _tokenize(state)
        has_neg = bool(tokens_s & NEGATION_WORDS)
        if has_neg:
            return NoulDecision(id=question.id, probability=0.85, confidence=0.80)
        return NoulDecision(id=question.id, probability=0.15, confidence=0.80)

    # Generic fallback
    return NoulDecision(id=question.id, probability=0.50, confidence=0.50)


def heuristic_choice(question: ChoiceQuestion, state: str) -> ChoiceDecision:
    prompt_lower = question.prompt.lower()
    options = question.options

    # Categorize shell command action
    if "categor" in prompt_lower or "nature" in prompt_lower or "action" in prompt_lower:
        for pat in DESTRUCTIVE_SHELL_PATTERNS:
            if pat.search(state):
                selected = next((opt for opt in options if "destruct" in opt.lower() or "danger" in opt.lower()), options[0])
                return ChoiceDecision(id=question.id, selected=selected, probabilities={opt: (0.95 if opt == selected else 0.05 / max(1, len(options) - 1)) for opt in options}, confidence=0.95)
        for pat in SAFE_SHELL_PATTERNS:
            if pat.search(state):
                selected = next((opt for opt in options if "read" in opt.lower() or "inspect" in opt.lower() or "safe" in opt.lower() or "compile_test" in opt.lower()), options[0])
                return ChoiceDecision(id=question.id, selected=selected, probabilities={opt: (0.92 if opt == selected else 0.08 / max(1, len(options) - 1)) for opt in options}, confidence=0.92)

    # Memory relation choice: ("contradicts_and_supersedes", "reinforces", "orthogonal")
    if any(opt in options for opt in ["contradicts_and_supersedes", "reinforces", "orthogonal", "contradicts"]):
        tokens = _tokenize(state)
        has_neg = bool(tokens & NEGATION_WORDS)
        if has_neg:
            selected = next((o for o in options if "contradict" in o.lower()), options[0])
            conf = 0.85
        else:
            selected = next((o for o in options if "reinforce" in o.lower()), options[0])
            conf = 0.75
        probs = {opt: (conf if opt == selected else (1.0 - conf) / max(1, len(options) - 1)) for opt in options}
        return ChoiceDecision(id=question.id, selected=selected, probabilities=probs, confidence=conf)

    # Uniform fallback distribution
    uniform = 1.0 / len(options) if options else 1.0
    return ChoiceDecision(id=question.id, selected=options[0] if options else "", probabilities={opt: uniform for opt in options}, confidence=0.40)


def heuristic_score(question: ScoreQuestion, state: str) -> ScoreDecision:
    scale = question.scale

    # Extract goal and chunk lines if present
    goal_match = re.search(r"GOAL:\s*(.*?)(?:\n|$)", state, re.IGNORECASE)
    goal_str = goal_match.group(1) if goal_match else question.prompt
    chunk_match = re.search(r"CHUNK:\s*([\s\S]*)", state, re.IGNORECASE)
    chunk_str = chunk_match.group(1) if chunk_match else state

    goal_tokens = _tokenize(goal_str)
    chunk_tokens = _tokenize(chunk_str)
    overlap = len(goal_tokens & chunk_tokens)

    if isinstance(scale[0], int):
        int_scale = [int(s) for s in scale]
        min_s = min(int_scale)
        max_s = max(int_scale)
        if overlap >= 3:
            score = max_s
        elif overlap >= 1:
            score = (min_s + max_s) // 2
        else:
            score = min_s
    else:
        score = scale[-1] if overlap >= 2 else scale[0]

    probs = {str(s): (0.80 if s == score else 0.20 / max(1, len(scale) - 1)) for s in scale}
    return ScoreDecision(id=question.id, score=score, probabilities=probs, confidence=0.70)


def evaluate_heuristics(state: str, questions: Sequence[Question]) -> DecisionBatch:
    decisions: Dict[str, Decision] = {}
    for q in questions:
        if isinstance(q, NoulQuestion) or getattr(q, "type", "") == "noul":
            decisions[q.id] = heuristic_noul(q, state)
        elif isinstance(q, ChoiceQuestion) or getattr(q, "type", "") == "choice":
            decisions[q.id] = heuristic_choice(q, state)
        elif isinstance(q, ScoreQuestion) or getattr(q, "type", "") == "score":
            decisions[q.id] = heuristic_score(q, state)
    return DecisionBatch(state=state, decisions=decisions, latency_ms=0.5, is_fallback=True)
