"""Standard library HTTP client for Jev (TypeSafe AI) System 1 decisions.

Zero external dependencies. Works offline and online.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Union

from .fallback import evaluate_heuristics
from .primitives import (
    ChoiceDecision,
    Decision,
    DecisionBatch,
    NoulDecision,
    Question,
    ScoreDecision,
)

logger = logging.getLogger(__name__)

DEFAULT_TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/decide"


class JevClient:
    """Client for TypeSafe AI's Jev model.

    Evaluates arbitrary typed questions against a shared state in a single parallel pass.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_s: float = 2.0,
        allow_fallback: bool = True,
        offline_mode: bool = False,
    ) -> None:
        self.offline_mode = offline_mode or os.environ.get("JEV_OFFLINE_MODE", "").lower() in ("1", "true", "yes")
        env_key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")
        self.api_key = api_key if api_key is not None else env_key
        self.base_url = (
            base_url
            or os.environ.get("JEV_ENDPOINT_URL")
            or DEFAULT_TYPESAFE_ENDPOINT
        )
        self.timeout_s = timeout_s
        self.allow_fallback = allow_fallback

    @property
    def is_configured(self) -> bool:
        if self.offline_mode:
            return False
        return bool(self.api_key and self.api_key.strip() and self.api_key not in ("mock", "offline"))

    def evaluate(
        self,
        state: str,
        questions: Sequence[Question],
        *,
        model: str = "jev-1",
    ) -> DecisionBatch:
        """Evaluate a batch of questions against the given state."""
        if not questions:
            return DecisionBatch(state=state, decisions={}, latency_ms=0.0)

        # If not configured or offline, fall back directly
        if not self.is_configured:
            if self.allow_fallback:
                return evaluate_heuristics(state, questions)
            raise ValueError("Jev API key is not configured and allow_fallback=False")

        payload = {
            "model": model,
            "state": state,
            "questions": [q.to_dict() for q in questions],
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "jev-decision-python/0.1.0",
        }

        req = urllib.request.Request(self.base_url, data=data, headers=headers, method="POST")
        start_t = time.perf_counter()

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                status_code = resp.status
                body = resp.read().decode("utf-8")
                elapsed_ms = (time.perf_counter() - start_t) * 1000.0

                if status_code != 200:
                    raise urllib.error.HTTPError(
                        self.base_url, status_code, f"HTTP {status_code}: {body}", resp.headers, None
                    )

                raw_data = json.loads(body)
                decisions = self._parse_decisions(raw_data)
                return DecisionBatch(
                    state=state,
                    decisions=decisions,
                    latency_ms=elapsed_ms,
                    is_fallback=False,
                    raw_response=raw_data,
                )

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            logger.warning("Jev request failed (%s); using heuristic fallback", exc)
            if self.allow_fallback:
                batch = evaluate_heuristics(state, questions)
                batch.latency_ms = elapsed_ms
                return batch
            raise

    def _parse_decisions(self, data: Dict[str, Any]) -> Dict[str, Decision]:
        decisions: Dict[str, Decision] = {}
        raw_decisions = data.get("decisions", {})

        for q_id, val in raw_decisions.items():
            q_type = val.get("type")
            conf = float(val.get("confidence", 1.0))
            if q_type == "noul":
                decisions[q_id] = NoulDecision(
                    id=q_id,
                    probability=float(val.get("probability", 0.0)),
                    confidence=conf,
                )
            elif q_type == "choice":
                probs = {k: float(v) for k, v in val.get("probabilities", {}).items()}
                decisions[q_id] = ChoiceDecision(
                    id=q_id,
                    selected=str(val.get("selected", "")),
                    probabilities=probs,
                    confidence=conf,
                )
            elif q_type == "score":
                probs = {str(k): float(v) for k, v in val.get("probabilities", {}).items()}
                decisions[q_id] = ScoreDecision(
                    id=q_id,
                    score=val.get("score", 0),
                    probabilities=probs,
                    confidence=conf,
                )

        return decisions
