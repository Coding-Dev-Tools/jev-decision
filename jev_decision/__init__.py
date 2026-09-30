"""Portable advisory TypeSafe Jev decisions with optional MCP and OS credentials."""

from ._version import __version__
from .client import DEFAULT_MODEL, JevClient, normalize_questions, validate_response, validate_state
from .fallback import evaluate_heuristics
from .harness_guards import (
    classify_memory_relation,
    guard_bash_command,
    prune_tool_output,
    verify_turn_completion,
)
from .primitives import (
    DEFAULT_CALIBRATION,
    CalibrationTier,
    ChoiceDecision,
    ChoiceQuestion,
    DecisionBatch,
    NoulDecision,
    NoulQuestion,
    Question,
    QuestionType,
    ScoreDecision,
    ScoreQuestion,
)

__all__ = [
    "__version__",
    "JevClient",
    "DEFAULT_MODEL",
    "normalize_questions",
    "validate_response",
    "validate_state",
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
    "Question",
    "evaluate_heuristics",
    "guard_bash_command",
    "prune_tool_output",
    "verify_turn_completion",
    "classify_memory_relation",
]
