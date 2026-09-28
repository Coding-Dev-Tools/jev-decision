"""Bounded, advisory TypeSafe Jev client with shared credential and spend policy.

No provider error becomes a synthetic judgment. Construction never contacts
TypeSafe. Transport injection exercises the same serialization and validation
as production without weakening the official endpoint allowlist.
"""

from __future__ import annotations

import copy
import hashlib
import http.client
import json
import math
import os
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from .primitives import (
    ChoiceDecision,
    ChoiceQuestion,
    Decision,
    DecisionBatch,
    NoulDecision,
    NoulQuestion,
    ScoreDecision,
    ScoreQuestion,
)

DEFAULT_TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.13.0"
MAX_QUESTIONS = 128
MAX_INPUT_TOKENS = 64000
PROBABILITY_TOLERANCE = 1e-3
_PINNED_MODEL = re.compile(r"jev-[0-9]+\.[0-9]+\.[0-9]+\Z")
Transport = Callable[[urllib.request.Request, float, int], Tuple[int, bytes]]


def _finite(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _json_value(value: Any, depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("invalid_json_value")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _json_value(item, depth + 1)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValueError("invalid_json_value")


def validate_state(state: Any) -> None:
    """Validate a nonempty string, object, or array, without changing its content."""
    if not isinstance(state, (str, dict, list)) or not state:
        raise ValueError("invalid_state")
    if isinstance(state, str) and not state.strip():
        raise ValueError("invalid_state")
    _json_value(state)


def _description(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and any(char.isalpha() for char in value)
    if isinstance(value, list):
        return bool(value) and any(_description(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and any(_description(item) for item in value.values())
    return False


def normalize_questions(questions: Any) -> Dict[str, Any]:
    """Return native questions; invalid caller data raises a content-free ValueError."""
    try:
        if isinstance(questions, dict):
            native = copy.deepcopy(questions)
        elif isinstance(questions, (list, tuple)):
            native = {}
            for question in questions:
                if not isinstance(question, (NoulQuestion, ChoiceQuestion, ScoreQuestion)):
                    raise ValueError("invalid_question")
                if not _text(question.id) or question.id in native:
                    raise ValueError("invalid_question_id")
                native[question.id] = copy.deepcopy(question.to_wire())
        else:
            raise ValueError("invalid_questions")
        if not 1 <= len(native) <= MAX_QUESTIONS:
            raise ValueError("invalid_questions")
        _json_value(native)
        for question_id, question in native.items():
            if not _text(question_id) or len(question_id) > 200:
                raise ValueError("invalid_question_id")
            if not isinstance(question, dict):
                raise ValueError("invalid_question")
            if not {"type", "instructions"} <= question.keys():
                raise ValueError("invalid_question")
            if not question.keys() <= {"type", "instructions", "criteria"}:
                raise ValueError("invalid_question")
            instructions = question["instructions"]
            if not isinstance(instructions, (str, dict, list)) or not instructions:
                raise ValueError("invalid_instructions")
            if isinstance(instructions, str) and not instructions.strip():
                raise ValueError("invalid_instructions")
            kind, criteria = question["type"], question.get("criteria")
            if kind == "choice":
                if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 255:
                    raise ValueError("invalid_choice_criteria")
                if any(not _text(key) or not _description(value) for key, value in criteria.items()):
                    raise ValueError("invalid_choice_criteria")
            elif kind == "score":
                if not isinstance(criteria, list) or not 2 <= len(criteria) <= 10:
                    raise ValueError("invalid_score_criteria")
                if any(not _description(level) for level in criteria):
                    raise ValueError("invalid_score_criteria")
                serialized = [json.dumps(level, sort_keys=True) for level in criteria]
                if len(set(serialized)) != len(serialized):
                    raise ValueError("duplicate_score_criteria")
            elif kind == "noul":
                if "criteria" in question and (
                    not isinstance(criteria, dict) or not criteria
                    or not criteria.keys() <= {"true", "false"}
                    or any(not _description(value) for value in criteria.values())
                ):
                    raise ValueError("invalid_noul_criteria")
            else:
                raise ValueError("invalid_question_type")
        return native
    except (TypeError, RecursionError, OverflowError, UnicodeError):
        raise ValueError("invalid_questions") from None


def _probability(value: Any) -> float:
    if not _finite(value) or not 0 <= value <= 1:
        raise ValueError("invalid_probability")
    return float(value)


def _distribution(value: Any, keys: Any) -> Dict[str, float]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("invalid_probability_keys")
    probabilities = {key: _probability(item) for key, item in value.items()}
    if not math.isclose(math.fsum(probabilities.values()), 1.0,
                        abs_tol=PROBABILITY_TOLERANCE, rel_tol=0):
        raise ValueError("invalid_probability_sum")
    return probabilities


def _usage(payload: Dict[str, Any]) -> Dict[str, Optional[int]]:
    result: Dict[str, Optional[int]] = {"input_tokens": None, "output_tokens": None}
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return result
    for name in result:
        value = usage.get(name)
        if type(value) is int and value >= 0:
            result[name] = value
    return result


def validate_response(payload: Any, questions: Dict[str, Any], model: str) -> Dict[str, Decision]:
    """Require all requested typed answers and a matching pinned model."""
    if not isinstance(payload, dict):
        raise ValueError("invalid_response")
    _json_value(payload)
    if payload.get("model") != model:
        raise ValueError("model_mismatch")
    answers = payload.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise ValueError("invalid_answer_keys")
    decisions: Dict[str, Decision] = {}
    for question_id, question in questions.items():
        answer = answers[question_id]
        kind = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise ValueError("invalid_answer_type")
        if kind == "noul":
            if set(answer) != {"type", "noul"}:
                raise ValueError("invalid_answer_fields")
            decisions[question_id] = NoulDecision(question_id, _probability(answer["noul"]))
            continue
        required = {"type", "confidence", "probabilities", kind}
        if kind == "score":
            required.add("legend")
        if set(answer) != required:
            raise ValueError("invalid_answer_fields")
        confidence = _probability(answer["confidence"])
        if kind == "choice":
            probabilities = _distribution(answer["probabilities"], question["criteria"])
            selected = answer["choice"]
            if not isinstance(selected, str) or selected not in probabilities:
                raise ValueError("invalid_choice")
            if max(probabilities.values()) - probabilities[selected] > PROBABILITY_TOLERANCE:
                raise ValueError("choice_probability_mismatch")
            decisions[question_id] = ChoiceDecision(question_id, selected, probabilities, confidence)
        else:
            legend = {str(index): level for index, level in enumerate(question["criteria"])}
            if answer["legend"] != legend:
                raise ValueError("invalid_legend")
            probabilities = _distribution(answer["probabilities"], legend)
            score = answer["score"]
            if not _finite(score) or not 0 <= score <= len(legend) - 1:
                raise ValueError("invalid_score")
            weighted = math.fsum(int(key) * value for key, value in probabilities.items())
            if not math.isclose(score, weighted, abs_tol=PROBABILITY_TOLERANCE, rel_tol=0):
                raise ValueError("score_probability_mismatch")
            decisions[question_id] = ScoreDecision(
                question_id, float(score), probabilities, confidence, copy.deepcopy(legend),
            )
    return decisions


def _unique_object(pairs: Any) -> Dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("nonfinite_json_number")


def _decode(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object,
                      parse_constant=_invalid_constant)


class _ResponseTooLarge(Exception):
    pass


def _http_transport(request: urllib.request.Request, timeout_s: float,
                    max_response_bytes: int) -> Tuple[int, bytes]:
    """Direct official HTTPS only: no proxy discovery and no redirect following."""
    if request.full_url != DEFAULT_TYPESAFE_ENDPOINT:
        raise ValueError("endpoint_not_allowlisted")
    deadline = time.monotonic() + timeout_s
    connection = http.client.HTTPSConnection("api.typesafe.ai", timeout=timeout_s)
    active_sockets = []

    def abort() -> None:
        # Socket timeouts alone reset on each successful read. A peer sending
        # headers or body bytes slowly must not keep a request alive indefinitely.
        for stream in active_sockets + [connection.sock]:
            if stream is not None:
                try:
                    stream.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        connection.close()

    timer = threading.Timer(timeout_s, abort)
    timer.daemon = True
    timer.start()
    try:
        connection.request("POST", "/v1/systemone", body=request.data,
                           headers=dict(request.header_items()))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        response = connection.getresponse()
        stream = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if stream is not None:
            active_sockets.append(stream)
        if response.status != 200:
            # Never retain error bodies: some services echo sensitive request data.
            return response.status, b""
        body = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            part = response.read1(min(65536, max_response_bytes + 1 - len(body)))
            if not part:
                break
            body.extend(part)
            if len(body) > max_response_bytes:
                raise _ResponseTooLarge()
        return response.status, bytes(body)
    finally:
        timer.cancel()
        connection.close()


def _bounded_transport(transport: Transport, request: urllib.request.Request,
                       timeout_s: float, response_limit: int) -> Tuple[int, bytes]:
    """Bound DNS, TLS, and body reading by one wall-clock deadline.

    A timed-out attempt keeps its spend reservation because it may have reached
    the provider. A daemon worker cannot hold process shutdown open.
    """
    result: queue.Queue = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result.put((True, transport(request, timeout_s, response_limit)))
        except Exception as exc:
            result.put((False, exc))

    threading.Thread(target=run, daemon=True, name="jev-http").start()
    try:
        success, value = result.get(timeout=max(0.000001, timeout_s))
    except queue.Empty:
        raise TimeoutError() from None
    if not success:
        raise value
    return value


def _http_error(status: int) -> Tuple[str, bool]:
    if 300 <= status < 400:
        return "redirect_rejected", False
    if status in (401, 403):
        return "authentication_error", False
    if status == 408:
        return "timeout", True
    if status == 429:
        return "rate_limited", True
    return "provider_error", status in (500, 502, 503, 504)


class JevClient:
    """Shared-policy client. Provider failures preserve the normal LLM workflow."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_s: Optional[float] = None,
        allow_fallback: bool = False,
        offline_mode: bool = False,
        *,
        model: Optional[str] = None,
        transport: Optional[Transport] = None,
        runtime: Any = None,
        budget_ledger: Any = None,
        cache_size: int = 64,
    ) -> None:
        self._configuration_error: Optional[str] = None
        self._api_key: Optional[str] = None
        self._runtime = runtime
        self._ledger = budget_ledger
        self._transport = transport or _http_transport
        self._cache: OrderedDict = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_size = max(0, min(cache_size, 256)) if type(cache_size) is int else 64
        self.allow_fallback = allow_fallback  # Compatibility only; never enables synthetic judgments.
        self.offline_mode = offline_mode or os.environ.get("JEV_OFFLINE_MODE", "").lower() in (
            "1", "true", "yes",
        )
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url or os.environ.get("JEV_ENDPOINT_URL") or DEFAULT_TYPESAFE_ENDPOINT
        self.timeout_s = 5.0 if timeout_s is None else timeout_s
        try:
            from .credentials import CredentialError, load_api_key
            from .policy import validate_endpoint
            from .runtime import RuntimeConfig

            self._runtime = runtime or RuntimeConfig.load()
            if not isinstance(self._runtime, RuntimeConfig):
                raise ValueError("invalid_runtime_config")
            self.model = model or self._runtime.model
            self.base_url = base_url or os.environ.get("JEV_ENDPOINT_URL") or self._runtime.endpoint
            self.timeout_s = self._runtime.timeout_s if timeout_s is None else timeout_s
            validate_endpoint(self.base_url)
            if not _finite(self.timeout_s) or not 0 < self.timeout_s <= 5.0:
                raise ValueError("invalid_timeout")
            if not isinstance(self.model, str) or not _PINNED_MODEL.fullmatch(self.model):
                raise ValueError("model_must_be_version_pinned")
            if self.model != self._runtime.model:
                raise ValueError("model_must_match_runtime")
            try:
                if not self.offline_mode:
                    self._api_key = api_key if api_key is not None else load_api_key(self._runtime)
            except CredentialError:
                self._configuration_error = "credential_unavailable"
        except Exception:
            self._configuration_error = "configuration_error"

    @property
    def runtime(self) -> Any:
        """Public configuration for harness policy/status; it contains no key."""
        return self._runtime

    @property
    def is_configured(self) -> bool:
        return bool(not self._configuration_error and not self.offline_mode
                    and self._valid_key() and getattr(self._runtime, "enabled", False))

    def _valid_key(self) -> bool:
        return bool(
            isinstance(self._api_key, str) and 1 <= len(self._api_key) <= 4096
            and self._api_key.lower() not in ("mock", "offline")
            and all(33 <= ord(char) <= 126 for char in self._api_key)
        )

    def evaluate(self, state: Any, questions: Any, *, model: Optional[str] = None) -> DecisionBatch:
        started = time.monotonic()
        requested_model = model or self.model
        batch = DecisionBatch(requested_model=requested_model, request_id=str(uuid.uuid4()))

        def finish(error: Optional[str] = None) -> DecisionBatch:
            batch.error_code = error
            batch.latency_ms = max(0.0, (time.monotonic() - started) * 1000.0)
            return batch

        if self.offline_mode:
            batch.status = "offline"
            return finish("offline")
        if self._configuration_error:
            return finish(self._configuration_error)
        if not getattr(self._runtime, "enabled", False):
            return finish("runtime_disabled")
        if not self._api_key:
            return finish("missing_key")
        if not self._valid_key():
            return finish("configuration_error")
        if requested_model != self.model:
            return finish("invalid_request")
        try:
            from .policy import sanitize_state

            validate_state(state)
            checked = normalize_questions(questions)
            # Sanitize all string-bearing request values, including instructions.
            clean_state = sanitize_state(state, secrets=(self._api_key,))
            checked = normalize_questions({
                question_id: sanitize_state(question, secrets=(self._api_key,))
                for question_id, question in checked.items()
            })
            validate_state(clean_state)
            body = json.dumps(
                {"model": requested_model, "state": clean_state, "questions": checked},
                ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
            ).encode("utf-8")
        except Exception:
            return finish("invalid_request")
        if len(body) > self._runtime.max_request_bytes:
            return finish("request_too_large")
        escaped_key = json.dumps(self._api_key, ensure_ascii=False)[1:-1].encode("utf-8")
        if self._api_key.encode("utf-8") in body or escaped_key in body:
            return finish("credential_in_payload")

        fingerprint = hashlib.sha256(body).digest()
        with self._cache_lock:
            cached = self._cache.get(fingerprint)
            if cached is not None:
                self._cache.move_to_end(fingerprint)
                batch = copy.deepcopy(cached)
                batch.source = "cache"
                batch.attempts = 0
                batch.request_id = str(uuid.uuid4())
                batch.usage = {"input_tokens": 0, "output_tokens": 0}
                return finish()

        deadline = started + self.timeout_s
        # Leave room for the bounded SQLite settlement after HTTP completes.
        accounting_margin = min(0.25, self.timeout_s / 10)
        usages = []
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return finish("timeout")
            try:
                from .budget import BudgetExceeded, BudgetLedger

                if self._ledger is None:
                    self._ledger = BudgetLedger(self._runtime)
                reservation = self._ledger.reserve()
            except BudgetExceeded:
                return finish("budget_exhausted")
            except Exception:
                return finish("budget_unavailable")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    self._ledger.settle(reservation, token_count=None)
                except Exception:
                    pass
                return finish("timeout")
            batch.attempts += 1
            request = urllib.request.Request(
                self.base_url, data=body, method="POST",
                headers={"Authorization": "Bearer " + self._api_key,
                         "Content-Type": "application/json", "Accept": "application/json",
                         "User-Agent": "jev-decision-python/0.3.0"},
            )
            error, retryable, known_tokens = None, False, None
            usage: Dict[str, Optional[int]] = {"input_tokens": None, "output_tokens": None}
            decisions, resolved_model = {}, None
            try:
                status, response_body = _bounded_transport(
                    self._transport, request, max(0.000001, remaining - accounting_margin),
                    self._runtime.max_response_bytes,
                )
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                if type(status) is not int or not 100 <= status <= 599:
                    error = "invalid_response"
                elif status != 200:
                    error, retryable = _http_error(status)
                elif not isinstance(response_body, bytes):
                    error = "invalid_response"
                elif len(response_body) > self._runtime.max_response_bytes:
                    error = "response_too_large"
                else:
                    try:
                        payload = _decode(response_body)
                        decisions = validate_response(payload, checked, requested_model)
                        usage = _usage(payload)
                        resolved_model = requested_model
                        known_tokens = usage["input_tokens"]
                        if known_tokens is not None and known_tokens > MAX_INPUT_TOKENS:
                            # Account for a provider overrun rather than hiding the cost.
                            # The anomalous answer remains unusable.
                            error = "invalid_response"
                            decisions = {}
                    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
                        error = "model_mismatch" if str(exc) == "model_mismatch" else "invalid_response"
            except _ResponseTooLarge:
                error = "response_too_large"
            except urllib.error.HTTPError as exc:
                error, retryable = _http_error(exc.code)
                exc.close()
            except (TimeoutError, socket.timeout):
                error, retryable = "timeout", True
            except (OSError, http.client.HTTPException, urllib.error.URLError):
                error, retryable = "transport_error", True
            except Exception:
                error, retryable = "transport_error", False
            try:
                # Malformed and failed replies are charged conservatively at the reservation.
                self._ledger.settle(reservation, token_count=known_tokens)
            except Exception:
                return finish("budget_unavailable")
            usages.append(usage)
            batch.usage = {
                name: sum(item[name] for item in usages) if all(item[name] is not None for item in usages) else None
                for name in ("input_tokens", "output_tokens")
            }
            if error is None:
                batch.status, batch.source = "ok", "provider"
                batch.decisions, batch.resolved_model = decisions, resolved_model
                finish()
                if self._cache_size:
                    with self._cache_lock:
                        self._cache[fingerprint] = copy.deepcopy(batch)
                        self._cache.move_to_end(fingerprint)
                        while len(self._cache) > self._cache_size:
                            self._cache.popitem(last=False)
                return batch
            if not retryable or attempt or deadline - time.monotonic() <= 0.05:
                return finish(error)
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return finish("provider_error")
