"""Core primitives and typed decision representations for Jev (TypeSafe AI).

Supports:
- Noul: Calibrated binary probability (P(True)).
- Choice: Categorical distribution over discrete options.
- Score: Ordinal position on a bounded scale.
- Calibration profiles and safety tiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class QuestionType(str, Enum):
    NOUL = "noul"
    CHOICE = "choice"
    SCORE = "score"


@dataclass(frozen=True)
class CalibrationTier:
    """Confidence thresholds for automated execution vs escalation."""
    # High-stakes: destructive commands, external writes, secret changes
    tier_destructive: float = 0.95
    # Medium-stakes: loop completion, contradiction invalidation
    tier_loop_halt: float = 0.85
    # Low-stakes: context pruning, log truncation (permissive retention)
    tier_relevance_prune: float = 0.40


DEFAULT_CALIBRATION = CalibrationTier()


@dataclass
class NoulQuestion:
    id: str
    prompt: str
    type: str = field(default=QuestionType.NOUL.value, init=False)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type, "prompt": self.prompt}


@dataclass
class ChoiceQuestion:
    id: str
    prompt: str
    options: List[str]
    type: str = field(default=QuestionType.CHOICE.value, init=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "prompt": self.prompt,
            "options": list(self.options),
        }


@dataclass
class ScoreQuestion:
    id: str
    prompt: str
    scale: List[Union[int, str]]
    type: str = field(default=QuestionType.SCORE.value, init=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "prompt": self.prompt,
            "scale": list(self.scale),
        }


Question = Union[NoulQuestion, ChoiceQuestion, ScoreQuestion]


@dataclass
class NoulDecision:
    id: str
    probability: float
    confidence: float

    @property
    def is_true(self) -> bool:
        return self.probability >= 0.5


@dataclass
class ChoiceDecision:
    id: str
    selected: str
    probabilities: Dict[str, float]
    confidence: float


@dataclass
class ScoreDecision:
    id: str
    score: Union[int, str]
    probabilities: Dict[str, float]
    confidence: float


Decision = Union[NoulDecision, ChoiceDecision, ScoreDecision]


@dataclass
class DecisionBatch:
    state: str
    decisions: Dict[str, Decision]
    latency_ms: float
    is_fallback: bool = False
    raw_response: Optional[Dict[str, Any]] = None

    def get_noul(self, question_id: str) -> Optional[NoulDecision]:
        d = self.decisions.get(question_id)
        return d if isinstance(d, NoulDecision) else None

    def get_choice(self, question_id: str) -> Optional[ChoiceDecision]:
        d = self.decisions.get(question_id)
        return d if isinstance(d, ChoiceDecision) else None

    def get_score(self, question_id: str) -> Optional[ScoreDecision]:
        d = self.decisions.get(question_id)
        return d if isinstance(d, ScoreDecision) else None
