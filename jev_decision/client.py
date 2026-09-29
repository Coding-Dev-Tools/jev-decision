"""Bounded, advisory TypeSafe Jev client with shared credential and spend policy.

No provider error becomes a synthetic judgment. Construction never contacts
TypeSafe. Transport injection exercises the same serialization and validation
as production without weakening the official endpoint allowlist.
"""

from __future__ import annotations

import copy
import hashlib
import http.client
import inspect
import json
import math
import os
import queue
import random
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, Union

from .jsonutil import json_equal
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
MAX_SAFE_USAGE_INTEGER = 2**53 - 1  # Same exact integer range as the TypeScript client.
PROBABILITY_TOLERANCE = 1e-3
_PINNED_MODEL = re.compile(r"jev-[0-9]+\.[0-9]+\.[0-9]+\Z")
TransportResult = Union[Tuple[int, bytes], Tuple[int, bytes, Mapping[str, str]]]
Transport = Callable[[urllib.request.Request, float, int], TransportResult]


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
                if any(not _text(key) or (value is not None and not _description(value))
                       for key, value in criteria.items()):
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
    total = math.fsum(probabilities.values())
    bounds = _rounded_bounds(probabilities)
    rounded_valid = bounds is not None and total > 0 and (
        math.fsum(pair[0] for pair in bounds.values()) <= 1 + 1e-9
        and math.fsum(pair[1] for pair in bounds.values()) >= 1 - 1e-9)
    if not math.isclose(total, 1.0, abs_tol=PROBABILITY_TOLERANCE, rel_tol=0) and not rounded_valid:
        raise ValueError("invalid_probability_sum")
    return probabilities


def _rounded_bounds(probabilities: Dict[str, float]) -> Optional[Dict[str, Tuple[float, float]]]:
    """Observed native responses independently round displayed values to 2dp.

    Preserve the wire values. Do not renormalize them or treat the displayed
    weighted sum as an exact reconstruction of the provider's internal score.
    """
    if not all(abs(value * 100 - round(value * 100)) <= 1e-8 for value in probabilities.values()):
        return None
    return {key: (max(0.0, value - 0.005), min(1.0, value + 0.005))
            for key, value in probabilities.items()}


def _consistent_score(score: float, probabilities: Dict[str, float]) -> bool:
    bounds = _rounded_bounds(probabilities)
    if bounds is None:
        weighted = math.fsum(int(key) * value for key, value in probabilities.items())
        return math.isclose(score, weighted, abs_tol=PROBABILITY_TOLERANCE, rel_tol=0)
    lower = math.fsum(pair[0] for pair in bounds.values())
    if lower > 1 + 1e-9 or math.fsum(pair[1] for pair in bounds.values()) < 1 - 1e-9:
        return False
    def extreme(reverse: bool) -> float:
        remaining = max(0.0, 1 - lower)
        value = math.fsum(int(key) * pair[0] for key, pair in bounds.items())
        for key in sorted(bounds, key=int, reverse=reverse):
            amount = min(remaining, bounds[key][1] - bounds[key][0])
            value += int(key) * amount
            remaining -= amount
        return value
    return score + 0.005 >= extreme(False) - 1e-9 and score - 0.005 <= extreme(True) + 1e-9


def _usage(payload: Dict[str, Any]) -> Dict[str, Optional[int]]:
    result: Dict[str, Optional[int]] = {"input_tokens": None, "output_tokens": None}
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return result
    for name in result:
        value = usage.get(name)
        if type(value) in (int, float) and 0 <= value <= MAX_SAFE_USAGE_INTEGER and value == int(value):
            result[name] = int(value)
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
            if not json_equal(answer["legend"], legend):
                raise ValueError("invalid_legend")
            probabilities = _distribution(answer["probabilities"], legend)
            score = answer["score"]
            if not _finite(score) or not 0 <= score <= len(legend) - 1:
                raise ValueError("invalid_score")
            if not _consistent_score(score, probabilities):
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
                    max_response_bytes: int) -> TransportResult:
    """Direct official HTTPS only: no proxy discovery and no redirect following."""
    if request.full_url != DEFAULT_TYPESAFE_ENDPOINT:
        raise ValueError("endpoint_not_allowlisted")
    deadline = min(time.monotonic() + timeout_s,
                   getattr(request, "_jev_deadline", float("inf")))
    cancelled = getattr(request, "_jev_cancelled", None) or threading.Event()
    connection = http.client.HTTPSConnection("api.typesafe.ai", timeout=timeout_s)
    active_sockets = []

    def check_active() -> None:
        if cancelled.is_set() or time.monotonic() >= deadline:
            raise TimeoutError()

    def abort() -> None:
        cancelled.set()
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
        check_active()
        # DNS may outlive the wall-clock deadline. Connecting separately prevents
        # it from completing late and then sending a billable POST after timeout.
        connection.connect()
        check_active()
        # Closing a socket while HTTPConnection.send is about to run must not
        # trigger HTTPConnection's automatic reconnect behavior.
        connection.auto_open = 0
        original_send = getattr(connection, "send", None)
        if original_send is not None:
            def guarded_send(data: Any) -> None:
                check_active()
                original_send(data)
            connection.send = guarded_send
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
            hint = response.getheader("Retry-After")
            return response.status, b"", {"retry-after": hint} if hint is not None else {}
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
        return response.status, bytes(body), {}
    finally:
        timer.cancel()
        connection.close()


def _bounded_transport(transport: Transport, request: urllib.request.Request,
                       timeout_s: float, response_limit: int) -> TransportResult:
    """Bound DNS, TLS, and body reading by one wall-clock deadline.

    A timed-out attempt keeps its spend reservation because it may have reached
    the provider. A daemon worker cannot hold process shutdown open.
    """
    deadline = time.monotonic() + timeout_s
    cancelled = threading.Event()
    request._jev_deadline = deadline
    request._jev_cancelled = cancelled
    try:
        return _bounded_call(lambda: transport(request, timeout_s, response_limit), deadline)
    except TimeoutError:
        cancelled.set()
        raise


def _bounded_call(operation: Callable[[], Any], deadline: float) -> Any:
    """Bound cooperative operations and compatibility-injected implementations."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    result: queue.Queue = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            result.put((True, operation()))
        except Exception as exc:
            result.put((False, exc))

    threading.Thread(target=run, daemon=True, name="jev-bounded").start()
    try:
        success, value = result.get(timeout=max(0.000001, deadline - time.monotonic()))
    except queue.Empty:
        raise TimeoutError() from None
    if not success:
        raise value
    if time.monotonic() >= deadline:
        raise TimeoutError()
    return value


def _accounting_call(operation: Callable[..., Any], *args: Any,
                     deadline: float, **kwargs: Any) -> Any:
    def invoke() -> Any:
        # Keep existing injected ledgers usable while passing the absolute
        # deadline to production accounting and newer adapters.
        parameters = inspect.signature(operation).parameters
        if "deadline" in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            return operation(*args, deadline=deadline, **kwargs)
        return operation(*args, **kwargs)
    return _bounded_call(invoke, deadline)


def _retry_after(headers: Any) -> Optional[float]:
    if not isinstance(headers, Mapping):
        return None
    value = next((value for key, value in headers.items()
                  if isinstance(key, str) and key.lower() == "retry-after"), None)
    if not isinstance(value, str) or len(value) > 128:
        return None
    value = value.strip()
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        seconds = float(value)
        return seconds if math.isfinite(seconds) else None
    try:
        instant = parsedate_to_datetime(value)
        if instant.tzinfo is None:
            return None
        return max(0.0, (instant - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def _http_error(status: int) -> Tuple[str, bool]:
    if 300 <= status < 400:
        return "redirect_rejected", False
    if status in (401, 403):
        return "authentication_error", False
    if status == 408:
        return "timeout", True
    if status == 429:
        return "rate_limited", True
    return "provider_error", status in (500, 502, 503, 504, 529)


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
        self._ledger_lock = threading.Lock()
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
                if not self.offline_mode and self._runtime.enabled:
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
        from .credentials import _valid_key_format
        return _valid_key_format(self._api_key)

    def _initialize_ledger(self, *, deadline: float) -> Any:
        from .budget import BudgetLedger
        if not self._ledger_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise TimeoutError()
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            if self._ledger is None:
                self._ledger = BudgetLedger(self._runtime, deadline=deadline)
            return self._ledger
        finally:
            self._ledger_lock.release()

    def evaluate(self, state: Any, questions: Any, *, model: Optional[str] = None,
                 deadline_monotonic: Optional[float] = None) -> DecisionBatch:
        started = time.monotonic()
        deadline = started + self.timeout_s if _finite(self.timeout_s) else started
        if deadline_monotonic is not None and _finite(deadline_monotonic):
            deadline = min(deadline, deadline_monotonic)
        requested_model = model or self.model
        batch = DecisionBatch(requested_model=requested_model, request_id=str(uuid.uuid4()))

        def finish(error: Optional[str] = None) -> DecisionBatch:
            if error is None and time.monotonic() >= deadline:
                batch.status, batch.source = "unavailable", "none"
                batch.decisions, batch.resolved_model = {}, None
                error = "timeout"
            batch.error_code = error
            batch.latency_ms = max(0.0, (time.monotonic() - started) * 1000.0)
            return batch

        if self.offline_mode:
            batch.status = "offline"
            return finish("offline")
        if self._configuration_error:
            return finish(self._configuration_error)
        if deadline_monotonic is not None and not _finite(deadline_monotonic):
            return finish("invalid_request")
        if not getattr(self._runtime, "enabled", False):
            return finish("runtime_disabled")
        if not self._api_key:
            return finish("missing_key")
        if not self._valid_key():
            return finish("configuration_error")
        if requested_model != self.model:
            return finish("invalid_request")
        if time.monotonic() >= deadline:
            return finish("timeout")
        def prepare() -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, Dict[str, str]], Dict[str, Any], bytes]:
            from .policy import sanitize_excerpt, sanitize_state

            validate_state(state)
            checked = normalize_questions(questions)
            # Sanitize all string-bearing request values, including instructions.
            clean_state = sanitize_state(state, secrets=(self._api_key,))
            wire_questions, original_ids, original_choices, original_legends = {}, {}, {}, {}
            for question_id, question in checked.items():
                wire_id = sanitize_excerpt(question_id, secrets=(self._api_key,))
                if wire_id in original_ids:
                    raise ValueError("ambiguous_question_ids")
                original_ids[wire_id] = question_id
                wire_questions[wire_id] = sanitize_state(question, secrets=(self._api_key,))
                if question["type"] == "choice":
                    original_choices[wire_id] = {
                        sanitize_excerpt(label, secrets=(self._api_key,)): label
                        for label in question["criteria"]
                    }
                elif question["type"] == "score":
                    original_legends[wire_id] = {
                        str(index): level for index, level in enumerate(question["criteria"])
                    }
            checked = normalize_questions(wire_questions)
            validate_state(clean_state)
            body = json.dumps(
                {"model": requested_model, "state": clean_state, "questions": checked},
                ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
            ).encode("utf-8")
            return checked, original_ids, original_choices, original_legends, body
        try:
            checked, original_ids, original_choices, original_legends, body = _bounded_call(prepare, deadline)
        except TimeoutError:
            return finish("timeout")
        except Exception:
            return finish("invalid_request")
        if len(body) > self._runtime.max_request_bytes:
            return finish("request_too_large")
        escaped_key = json.dumps(self._api_key, ensure_ascii=False)[1:-1].encode("utf-8")
        if self._api_key.encode("utf-8") in body or escaped_key in body:
            return finish("credential_in_payload")

        def restore_ids() -> DecisionBatch:
            for wire_id, decision in batch.decisions.items():
                decision.id = original_ids[wire_id]
                if isinstance(decision, ChoiceDecision):
                    labels = original_choices[wire_id]
                    decision.selected = labels[decision.selected]
                    decision.probabilities = {labels[key]: value for key, value in decision.probabilities.items()}
                elif isinstance(decision, ScoreDecision):
                    decision.legend = copy.deepcopy(original_legends[wire_id])
            batch.decisions = {original_ids[key]: value for key, value in batch.decisions.items()}
            return batch

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
                restore_ids()
                return finish()

        # Leave room for the bounded SQLite settlement after HTTP completes.
        accounting_margin = min(0.25, self.timeout_s / 10)
        usages = []
        for attempt in range(2):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return finish("timeout")
            try:
                from .budget import MAX_SETTLEMENT_TOKENS, BudgetDeadlineExceeded, BudgetExceeded

                if self._ledger is None:
                    _accounting_call(self._initialize_ledger, deadline=deadline)
                reservation = _accounting_call(self._ledger.reserve, deadline=deadline)
            except (TimeoutError, BudgetDeadlineExceeded):
                return finish("timeout")
            except BudgetExceeded:
                return finish("budget_exhausted")
            except Exception:
                return finish("budget_unavailable")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # The committed reservation already holds the worst-case amount.
                # Never spend more time trying to relabel it after expiration.
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
            retry_after = None
            try:
                response = _bounded_transport(
                    self._transport, request, max(0.000001, remaining - accounting_margin),
                    self._runtime.max_response_bytes,
                )
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                if not isinstance(response, tuple) or len(response) not in (2, 3):
                    raise ValueError("invalid_transport_response")
                status, response_body = response[:2]
                retry_after = _retry_after(response[2]) if len(response) == 3 else None
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
                        def parse_reply() -> Tuple[Dict[str, Decision], Dict[str, Optional[int]], bool]:
                            payload = _decode(response_body)
                            decisions = validate_response(payload, checked, requested_model)
                            reported = payload.get("usage")
                            count = reported.get("input_tokens") if isinstance(reported, dict) else None
                            overrun = (type(count) is int or type(count) is float and count.is_integer()) and count > MAX_INPUT_TOKENS
                            return decisions, _usage(payload), overrun
                        decisions, usage, usage_overrun = _bounded_call(parse_reply, deadline)
                        resolved_model = requested_model
                        known_tokens = usage["input_tokens"]
                        if usage_overrun:
                            # Account for a provider overrun rather than hiding the cost.
                            # The anomalous answer remains unusable.
                            error = "invalid_response"
                            decisions = {}
                            if known_tokens is not None and known_tokens > MAX_SETTLEMENT_TOKENS:
                                # Keep the provider's anomalous usage in the result,
                                # but retain an unknown hold instead of misreporting
                                # an accounting failure for an unsupported count.
                                known_tokens = None
                    except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
                        error = "model_mismatch" if str(exc) == "model_mismatch" else "invalid_response"
            except _ResponseTooLarge:
                error = "response_too_large"
            except urllib.error.HTTPError as exc:
                error, retryable = _http_error(exc.code)
                retry_after = _retry_after(exc.headers)
                exc.close()
            except (TimeoutError, socket.timeout):
                error, retryable = "timeout", True
            except (OSError, http.client.HTTPException, urllib.error.URLError):
                error, retryable = "transport_error", True
            except Exception:
                error, retryable = "transport_error", False
            try:
                # Malformed and failed replies are charged conservatively at the reservation.
                _accounting_call(self._ledger.settle, reservation, token_count=known_tokens,
                                 deadline=deadline)
            except (TimeoutError, BudgetDeadlineExceeded):
                return finish("timeout")
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
                if batch.status != "ok":
                    return batch
                if self._cache_size:
                    with self._cache_lock:
                        self._cache[fingerprint] = copy.deepcopy(batch)
                        self._cache.move_to_end(fingerprint)
                        while len(self._cache) > self._cache_size:
                            self._cache.popitem(last=False)
                # Cache only wire values; restore this caller's IDs, Choice labels
                # and Score legends after validating the sanitized response.
                return restore_ids()
            delay = max(random.uniform(0.05, 0.1), retry_after or 0.0)
            if not retryable or attempt or deadline - time.monotonic() <= delay:
                return finish(error)
            time.sleep(delay)
        return finish("provider_error")
