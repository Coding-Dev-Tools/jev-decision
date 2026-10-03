"""Explicit offline result for callers retaining the old fallback import.

Regexes and token overlap cannot establish authorization, task success, or a
calibrated probability. Offline callers receive no fabricated judgments.
"""

from __future__ import annotations

from typing import Any, Sequence

from .primitives import DecisionBatch, Question


def evaluate_heuristics(state: Any, questions: Sequence[Question]) -> DecisionBatch:
    """Return an explicit offline result; the caller retains its normal behavior."""
    return DecisionBatch(status="offline", source="none", error_code="offline")
