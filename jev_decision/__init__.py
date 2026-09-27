"""Jev System One Decision Engine & Harness Guardrails.

Zero-dependency client, typed primitives, calibration profiles, and
high-speed agent guardrails for Jev (TypeSafe AI).
"""

from .client import JevClient
from .fallback import evaluate_heuristics
from .harness_guards import (
    classify_memory_relation,
    guard_bash_command,
    prune_tool_output,
    verify_turn_completion,
)
from .primitives import (
    CalibrationTier,
    ChoiceDecision,
    ChoiceQuestion,
    DecisionBatch,
    DEFAULT_CALIBRATION,
    NoulDecision,
    NoulQuestion,
    Question,
    QuestionType,
    ScoreDecision,
    ScoreQuestion,
)

__version__ = "0.1.0"
__all__ = [
    "JevClient",
    "NoulQuestion",
    "ChoiceQuestion",
    "ScoreQuestion",
    "NoulDecision",
    "ChoiceDecision",
    "ScoreDecision",
    "DecisionBatch",
    "CalibrationTier",
    "DEFAULT_CALIBRATION",
    "QuestionType",
    "evaluate_heuristics",
    "guard_bash_command",
    "prune_tool_output",
    "verify_turn_completion",
    "classify_memory_relation",
]
