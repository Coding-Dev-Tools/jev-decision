"""Typed Jev questions and advisory results.

Score values are positions on an ordered descriptive rubric, including fractional
positions. Provider confidence describes a distribution, not proven accuracy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


class QuestionType(str, Enum):
    NOUL = "noul"
    CHOICE = "choice"
    SCORE = "score"


@dataclass(frozen=True)
class CalibrationTier:
    """Legacy advisory thresholds; none grants execution or completion authority."""

    tier_destructive: float = 0.95
    tier_loop_halt: float = 0.85
    tier_relevance_prune: float = 0.40


DEFAULT_CALIBRATION = CalibrationTier()


@dataclass
class NoulQuestion:
    id: str
    prompt: Any
    criteria: Optional[Dict[str, Any]] = None
    type: str = field(default=QuestionType.NOUL.value, init=False)

    def to_dict(self) -> Dict[str, Any]:
        result = {"id": self.id, "type": self.type, "prompt": self.prompt}
        if self.criteria is not None:
            result["criteria"] = self.criteria
        return result

    def to_wire(self) -> Dict[str, Any]:
        result = {"type": self.type, "instructions": self.prompt}
        if self.criteria is not None:
            result["criteria"] = self.criteria
        return result


@dataclass
class ChoiceQuestion:
    id: str
    prompt: Any
    options: Optional[List[str]] = None
    criteria: Optional[Dict[str, Any]] = None
    type: str = field(default=QuestionType.CHOICE.value, init=False)

    def to_dict(self) -> Dict[str, Any]:
        result = {"id": self.id, "type": self.type, "prompt": self.prompt}
        if self.criteria is not None:
            result["criteria"] = self.criteria
        elif self.options is not None:
            result["options"] = list(self.options)
        return result

    def to_wire(self) -> Dict[str, Any]:
        if self.criteria is not None and self.options is not None:
            if list(self.criteria) != self.options:
                raise ValueError("conflicting_choice_criteria")
        if self.options is not None and len(set(self.options)) != len(self.options):
            raise ValueError("duplicate_choice_options")
        return {
            "type": self.type,
            "instructions": self.prompt,
            "criteria": self.criteria if self.criteria is not None else {
                option: option for option in (self.options or [])
            },
        }


@dataclass
class ScoreQuestion:
    id: str
    prompt: Any
    scale: Optional[List[Any]] = None
    criteria: Optional[List[Any]] = None
    type: str = field(default=QuestionType.SCORE.value, init=False)

    def to_dict(self) -> Dict[str, Any]:
        result = {"id": self.id, "type": self.type, "prompt": self.prompt}
        if self.criteria is not None:
            result["criteria"] = self.criteria
        elif self.scale is not None:
            result["scale"] = list(self.scale)
        return result

    def to_wire(self) -> Dict[str, Any]:
        if self.criteria is not None and self.scale is not None and self.criteria != self.scale:
            raise ValueError("conflicting_score_criteria")
        return {
            "type": self.type,
            "instructions": self.prompt,
            "criteria": self.criteria if self.criteria is not None else self.scale,
        }


Question = Union[NoulQuestion, ChoiceQuestion, ScoreQuestion]


@dataclass
class NoulDecision:
    id: str
    probability: float
    confidence: Optional[float] = None

    @property
    def is_true(self) -> bool:
        """A probability threshold only; it is never execution authorization."""
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
    score: float
    probabilities: Dict[str, float]
    confidence: float
    legend: Dict[str, Any] = field(default_factory=dict)


Decision = Union[NoulDecision, ChoiceDecision, ScoreDecision]


@dataclass
class DecisionBatch:
    # Retained for Python compatibility; serialization deliberately excludes state.
    state: Any = None
    decisions: Dict[str, Decision] = field(default_factory=dict)
    latency_ms: float = 0.0
    is_fallback: bool = False
    raw_response: Optional[Dict[str, Any]] = field(default=None, repr=False)
    status: str = "unavailable"
    source: str = "none"
    requested_model: str = "jev-1.13.0"
    resolved_model: Optional[str] = None
    usage: Dict[str, Optional[int]] = field(default_factory=lambda: {
        "input_tokens": None, "output_tokens": None,
    })
    attempts: int = 0
    request_id: str = ""
    error_code: Optional[str] = None

    @property
    def fallback_reason(self) -> Optional[str]:
        """Compatibility alias for the content-free unavailable reason."""
        return self.error_code

    def get_noul(self, question_id: str) -> Optional[NoulDecision]:
        decision = self.decisions.get(question_id)
        return decision if isinstance(decision, NoulDecision) else None

    def get_choice(self, question_id: str) -> Optional[ChoiceDecision]:
        decision = self.decisions.get(question_id)
        return decision if isinstance(decision, ChoiceDecision) else None

    def get_score(self, question_id: str) -> Optional[ScoreDecision]:
        decision = self.decisions.get(question_id)
        return decision if isinstance(decision, ScoreDecision) else None

    def to_dict(self) -> Dict[str, Any]:
        """Return decisions under caller IDs, excluding state and raw bodies.

        IDs, Choice labels and Score legends intentionally preserve caller input;
        use non-sensitive labels and rubrics even though recognizable secrets are
        redacted on wire.
        """
        decisions = {}
        for question_id, decision in self.decisions.items():
            value = asdict(decision)
            value.pop("id", None)
            value["type"] = (
                "noul" if isinstance(decision, NoulDecision)
                else "choice" if isinstance(decision, ChoiceDecision) else "score"
            )
            decisions[question_id] = value
        return {
            "status": self.status,
            "source": self.source,
            "decisions": decisions,
            "requested_model": self.requested_model,
            "resolved_model": self.resolved_model,
            "usage": dict(self.usage),
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "request_id": self.request_id,
            "error_code": self.error_code,
            "is_fallback": self.is_fallback,
            "advisory_only": True,
        }
