"""Provider-free tests for the production serialization, validation and accounting path."""

import copy
import json
import threading
import time
import urllib.request
from decimal import Decimal

import pytest

from jev_decision import (
    ChoiceQuestion,
    JevClient,
    NoulQuestion,
    ScoreQuestion,
    normalize_questions,
)
from jev_decision.budget import (
    MAX_SETTLEMENT_TOKENS,
    NANODOLLARS_PER_DOLLAR,
    NANODOLLARS_PER_TOKEN,
    BudgetError,
    BudgetExceeded,
    BudgetLedger,
)
from jev_decision.client import (
    DEFAULT_MODEL,
    DEFAULT_TYPESAFE_ENDPOINT,
    MAX_SAFE_USAGE_INTEGER,
    _http_transport,
)
from jev_decision.runtime import RuntimeConfig

KEY = "fixture-only-key-no-provider-access"


class Ledger:
    def __init__(self):
        self.reservations = []
        self.settlements = []

    def reserve(self):
        value = len(self.reservations) + 1
        self.reservations.append(value)
        return value

    def settle(self, reservation, token_count=None):
        self.settlements.append((reservation, token_count))


def response_for(request):
    payload = json.loads(request.data)
    answers = {}
    for name, question in payload["questions"].items():
        kind = question["type"]
        if kind == "noul":
            answers[name] = {"type": kind, "noul": 0.8}
        elif kind == "choice":
            labels = list(question["criteria"])
            probabilities = {label: (0.75 if i == 0 else 0.25 / (len(labels) - 1))
                             for i, label in enumerate(labels)}
            answers[name] = {"type": kind, "choice": labels[0],
                             "probabilities": probabilities, "confidence": 0.5}
        else:
            levels = question["criteria"]
            probabilities = {str(i): (0.25 if i == 0 else 0.75 if i == 1 else 0.0)
                             for i in range(len(levels))}
            answers[name] = {
                "type": kind, "score": 0.75, "confidence": 0.5,
                "probabilities": probabilities,
                "legend": {str(i): level for i, level in enumerate(levels)},
            }
    return {"model": payload["model"], "answers": answers,
            "usage": {"input_tokens": 123, "output_tokens": 7}}


def wire(payload):
    return 200, json.dumps(payload).encode("utf-8")


@pytest.fixture(autouse=True)
def clear_ambient_configuration(monkeypatch):
    for name in ("JEV_OFFLINE_MODE", "JEV_ENDPOINT_URL", "TYPESAFE_API_KEY", "JEV_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def make_client(tmp_path):
    def factory(transport=None, **kwargs):
        ledger = kwargs.pop("budget_ledger", Ledger())
        config = kwargs.pop("runtime", RuntimeConfig(home=tmp_path, enabled=True))
        client = JevClient(
            api_key=kwargs.pop("api_key", KEY),
            runtime=config,
            budget_ledger=ledger,
            transport=transport or (lambda request, timeout, limit: wire(response_for(request))),
            **kwargs,
        )
        return client, ledger
    return factory


def noul():
    return [NoulQuestion("q", "Does the excerpt report an error?")]


def test_native_contract_and_fractional_score(make_client):
    captured = []

    def transport(request, timeout, limit):
        captured.append((request, timeout, limit))
        return wire(response_for(request))

    client, ledger = make_client(transport)
    questions = [
        NoulQuestion("binary", {"question": "Does the text report an error?"}),
        ChoiceQuestion("category", "Classify the report",
                       criteria={"bug": "Reports broken behavior", "question": "Asks for information"}),
        ScoreQuestion("severity", "How severe is the reported issue?",
                      criteria=["Cosmetic issue", "Feature does not work", "Data loss"]),
    ]
    batch = client.evaluate({"excerpt": "The button has a typo.", "count": 1}, questions)
    assert batch.status == "ok"
    assert batch.source == "provider"
    assert batch.resolved_model == DEFAULT_MODEL
    assert batch.get_score("severity").score == 0.75
    assert batch.get_score("severity").legend["1"] == "Feature does not work"
    assert batch.get_noul("binary").confidence is None
    assert batch.usage == {"input_tokens": 123, "output_tokens": 7}
    assert ledger.settlements == [(1, 123)]
    request, timeout, limit = captured[0]
    assert request.full_url == DEFAULT_TYPESAFE_ENDPOINT
    assert request.get_header("Authorization") == "Bearer " + KEY
    payload = json.loads(request.data)
    assert payload["model"] == DEFAULT_MODEL
    assert isinstance(payload["questions"], dict)
    assert payload["questions"]["severity"]["criteria"][0] == "Cosmetic issue"
    assert 0 < timeout <= 5
    assert limit == 262144
    public = batch.to_dict()
    assert "state" not in public and "raw_response" not in public
    assert public["decisions"]["binary"]["confidence"] is None
    assert "typo" not in json.dumps(public)
    assert KEY not in json.dumps(public)


@pytest.mark.parametrize("questions", [
    [],
    [NoulQuestion("", "Question")],
    [NoulQuestion("q", "Question"), NoulQuestion("q", "Duplicate")],
    [ChoiceQuestion("q", "Question", options=["same", "same"])],
    [ScoreQuestion("q", "Question", scale=[0, 1, 2])],
    [ScoreQuestion("q", "Question", criteria=["0", "1"])],
    [ScoreQuestion("q", "Question", criteria=["Duplicate", "Duplicate"])],
    {"q": {"type": "score", "instructions": "Question", "criteria": ["One"]}},
    {"q": {"type": "choice", "instructions": "Question", "criteria": {}}},
    {"q": {"type": "noul", "instructions": "   "}},
    {"q": {"type": "unknown", "instructions": "Question"}},
    {"q": {"type": "noul", "instructions": "Question", "extra": "unsupported"}},
])
def test_invalid_questions_never_reserve_or_contact_provider(make_client, questions):
    client, ledger = make_client()
    with pytest.raises(ValueError):
        normalize_questions(questions)
    batch = client.evaluate("An excerpt", questions)
    assert batch.status == "unavailable"
    assert batch.error_code == "invalid_request"
    assert not ledger.reservations


@pytest.mark.parametrize("state", [None, 4, True, "", " ", {"bad": float("nan")}, {1: "value"}])
def test_invalid_state_never_reserves(make_client, state):
    client, ledger = make_client()
    assert client.evaluate(state, noul()).error_code == "invalid_request"
    assert not ledger.reservations


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("model"),
    lambda p: p["answers"].pop("q"),
    lambda p: p["answers"].update({"extra": {"type": "noul", "noul": 0.3}}),
    lambda p: p["answers"]["q"].update({"noul": 4.0}),
    lambda p: p["answers"]["q"].update({"noul": True}),
    lambda p: p["answers"]["q"].update({"noul": "0.8"}),
    lambda p: p["answers"]["q"].update({"noul": float("inf")}),
    lambda p: p["answers"]["q"].update({"type": "choice"}),
    lambda p: p["answers"]["q"].update({"confidence": 1.0}),
])
def test_invalid_answers_have_no_decisions_and_retain_reservation(make_client, mutate):
    def transport(request, *_):
        payload = response_for(request)
        mutate(payload)
        return wire(payload)

    client, ledger = make_client(transport)
    batch = client.evaluate("An excerpt", noul())
    assert batch.status == "unavailable"
    assert batch.error_code in ("invalid_response", "model_mismatch")
    assert not batch.decisions
    assert ledger.settlements == [(1, None)]


@pytest.mark.parametrize("mutate", [
    lambda a: a.update({"score": 0}),
    lambda a: a.update({"score": 10}),
    lambda a: a.update({"confidence": -1}),
    lambda a: a["probabilities"].update({"0": 0.8}),
    lambda a: a["probabilities"].update({"2": 0.0}),
    lambda a: a["legend"].update({"0": "Wrong rubric"}),
    lambda a: a.pop("legend"),
])
def test_score_validation(make_client, mutate):
    def transport(request, *_):
        payload = response_for(request)
        mutate(payload["answers"]["q"])
        return wire(payload)

    client, ledger = make_client(transport)
    batch = client.evaluate("An excerpt", [
        ScoreQuestion("q", "Relevance to the task", criteria=["Unrelated", "Required evidence"]),
    ])
    assert batch.error_code == "invalid_response"
    assert not batch.decisions
    assert ledger.settlements == [(1, None)]


def test_choice_must_match_complete_distribution(make_client):
    def transport(request, *_):
        payload = response_for(request)
        payload["answers"]["q"]["choice"] = "second"
        return wire(payload)

    client, _ = make_client(transport)
    assert client.evaluate("An excerpt", [
        ChoiceQuestion("q", "Which category?", options=["first", "second"]),
    ]).error_code == "invalid_response"


@pytest.mark.parametrize("usage", [None, {"input_tokens": -1, "output_tokens": True}, "invalid"])
def test_unknown_usage_stays_unknown_without_discarding_valid_answers(make_client, usage):
    def transport(request, *_):
        payload = response_for(request)
        payload["usage"] = usage
        return wire(payload)

    client, ledger = make_client(transport)
    batch = client.evaluate("An excerpt", noul())
    assert batch.status == "ok"
    assert batch.usage == {"input_tokens": None, "output_tokens": None}
    assert ledger.settlements == [(1, None)]


def test_provider_usage_overrun_is_recorded_not_hidden(make_client):
    def transport(request, *_):
        payload = response_for(request)
        payload["usage"]["input_tokens"] = 70000
        return wire(payload)

    client, ledger = make_client(transport)
    batch = client.evaluate("An excerpt", noul())
    assert batch.error_code == "invalid_response"
    assert ledger.settlements == [(1, 70000)]
    assert batch.usage["input_tokens"] == 70000
    assert not batch.decisions


def test_duplicate_json_keys_rejected(make_client):
    client, ledger = make_client(lambda *_: (200, b'{"model":"jev-1.13.0","model":"jev-1.13.0"}'))
    assert client.evaluate("An excerpt", noul()).error_code == "invalid_response"
    assert ledger.settlements == [(1, None)]


def test_sanitized_request_and_detached_cache(make_client):
    captured = []

    def transport(request, *_):
        captured.append(request.data)
        return wire(response_for(request))

    client, ledger = make_client(transport)
    original = {"excerpt": "reference " + KEY, "password": "private-example"}
    first = client.evaluate(original, noul())
    assert first.status == "ok"
    first.decisions["q"].probability = 0
    second = client.evaluate(copy.deepcopy(original), noul())
    assert second.source == "cache"
    assert second.get_noul("q").probability == 0.8
    assert second.usage == {"input_tokens": 0, "output_tokens": 0}
    assert second.attempts == 0
    assert first.request_id != second.request_id
    assert ledger.reservations == [1]
    assert len(captured) == 1
    assert KEY.encode() not in captured[0]
    assert b"private-example" not in captured[0]
    assert original["password"] == "private-example"


def test_transient_retry_reserves_every_attempt(make_client):
    calls = []

    def transport(request, *_):
        calls.append(1)
        return (503, b"sensitive error body") if len(calls) == 1 else wire(response_for(request))

    client, ledger = make_client(transport)
    batch = client.evaluate("An excerpt", noul())
    assert batch.status == "ok" and batch.attempts == 2
    assert ledger.reservations == [1, 2]
    assert ledger.settlements == [(1, None), (2, 123)]
    assert batch.usage == {"input_tokens": None, "output_tokens": None}


@pytest.mark.parametrize("status,code,retries", [
    (302, "redirect_rejected", 1),
    (401, "authentication_error", 1),
    (403, "authentication_error", 1),
    (408, "timeout", 2),
    (429, "rate_limited", 2),
    (503, "provider_error", 2),
    (529, "provider_error", 2),
])
def test_http_failure_content_is_never_exposed(make_client, status, code, retries, caplog):
    client, ledger = make_client(lambda *_: (status, (KEY + " provider echo").encode()))
    batch = client.evaluate("An excerpt", noul())
    assert batch.error_code == code
    assert batch.attempts == retries
    assert not batch.decisions
    assert KEY not in json.dumps(batch.to_dict())
    assert KEY not in caplog.text
    assert len(ledger.settlements) == retries
    assert all(tokens is None for _, tokens in ledger.settlements)


def test_unknown_transport_exception_is_content_free(make_client):
    def transport(*_):
        raise RuntimeError(KEY)

    client, ledger = make_client(transport)
    batch = client.evaluate("An excerpt", noul())
    assert batch.error_code == "transport_error"
    assert KEY not in json.dumps(batch.to_dict())
    assert ledger.settlements == [(1, None)]


def test_end_to_end_transport_deadline(make_client):
    released = threading.Event()

    def transport(request, *_):
        released.wait(1)
        return wire(response_for(request))

    client, ledger = make_client(transport, timeout_s=0.03)
    started = time.monotonic()
    try:
        batch = client.evaluate("An excerpt", noul())
        elapsed = time.monotonic() - started
        assert batch.error_code == "timeout"
        assert batch.attempts == 1
        assert elapsed < 0.3
        assert ledger.reservations == [1]
        # An expired call may leave the original worst-case reservation in place
        # rather than spend more deadline time relabeling it as unknown.
        assert ledger.settlements in ([], [(1, None)])
    finally:
        released.set()


def test_request_response_bounds(make_client):
    client, ledger = make_client()
    assert client.evaluate("a" * 24576, noul()).error_code == "request_too_large"
    assert not ledger.reservations
    client, ledger = make_client(lambda *_: (200, b"x" * 262145))
    assert client.evaluate("An excerpt", noul()).error_code == "response_too_large"
    assert ledger.settlements == [(1, None)]


def test_no_key_offline_and_fallback_flag_do_not_synthesize(make_client):
    for options, error in [
        ({"api_key": ""}, "missing_key"),
        ({"api_key": "", "allow_fallback": True}, "missing_key"),
        ({"offline_mode": True}, "offline"),
    ]:
        client, ledger = make_client(**options)
        batch = client.evaluate("All tests passed", noul())
        assert batch.error_code == error
        assert not batch.decisions
        assert not ledger.reservations


def test_model_pin_endpoint_and_disable_before_network(make_client, tmp_path):
    for options, error in [
        ({"base_url": "http://127.0.0.1:1"}, "configuration_error"),
        ({"model": "jev-latest"}, "configuration_error"),
        ({"model": "jev-1.13.1"}, "configuration_error"),
        ({"runtime": RuntimeConfig(home=tmp_path, enabled=False)}, "runtime_disabled"),
    ]:
        client, ledger = make_client(**options)
        assert client.evaluate("An excerpt", noul()).error_code == error
        assert not ledger.reservations
    client, ledger = make_client()
    assert client.evaluate("An excerpt", noul(), model="jev-latest").error_code == "invalid_request"
    assert not ledger.reservations


@pytest.mark.parametrize("failure,code", [
    (BudgetExceeded("budget exhausted"), "budget_exhausted"),
    (BudgetError("unavailable"), "budget_unavailable"),
])
def test_budget_denial_never_calls_transport(make_client, failure, code):
    class Deny(Ledger):
        def reserve(self):
            raise failure

    called = []
    client, _ = make_client(lambda *_: called.append(1), budget_ledger=Deny())
    assert client.evaluate("An excerpt", noul()).error_code == code
    assert not called


def test_settlement_failure_prevents_exposing_a_success(make_client):
    class FailSettlement(Ledger):
        def settle(self, *_args, **_kwargs):
            raise BudgetError("ledger unavailable")

    client, _ = make_client(budget_ledger=FailSettlement())
    batch = client.evaluate("An excerpt", noul())
    assert batch.error_code == "budget_unavailable"
    assert not batch.decisions


@pytest.mark.parametrize("tokens", [64001, MAX_SETTLEMENT_TOKENS, MAX_SETTLEMENT_TOKENS + 1,
                                  MAX_SAFE_USAGE_INTEGER, MAX_SAFE_USAGE_INTEGER + 1, 10**100],
                         ids=["above-provider-limit", "accounting-limit", "above-accounting-limit",
                              "safe-integer-limit", "unsafe-integer", "huge-count"])
def test_anomalous_usage_is_a_provider_error_with_conservative_accounting(tmp_path, tokens):
    config = RuntimeConfig(home=tmp_path, enabled=True)
    ledger = BudgetLedger(config)
    calls = []

    def transport(request, *_):
        calls.append(1)
        payload = response_for(request)
        payload["usage"] = {"input_tokens": tokens, "output_tokens": 3}
        return wire(payload)

    client = JevClient(api_key=KEY, runtime=config, budget_ledger=ledger, transport=transport)
    result = client.evaluate("An excerpt", noul())
    assert result.error_code == "invalid_response" and not result.decisions
    assert result.attempts == len(calls) == 1
    assert result.usage == {"input_tokens": tokens if tokens <= MAX_SAFE_USAGE_INTEGER else None, "output_tokens": 3}
    assert not client._cache
    status = ledger.status()  # The ledger is healthy even for unsupported counts.
    if tokens <= MAX_SETTLEMENT_TOKENS:
        assert status["settled_attempts"] == 1 and status["held_usd"] == 0
        assert status["known_spend_usd"] == tokens * NANODOLLARS_PER_TOKEN / NANODOLLARS_PER_DOLLAR
    else:
        assert status["unknown_attempts"] == 1 and status["known_spend_usd"] == 0
        assert status["held_usd"] == status["reservation_usd"]


def test_native_transport_does_not_redirect_or_read_error_bodies(monkeypatch):
    calls = []

    class Response:
        status = 302

        def getheader(self, _name):
            return None

        def read1(self, *_):
            pytest.fail("Error body must not be read")

    class Connection:
        sock = None

        def __init__(self, host, timeout):
            calls.append(("connect", host, timeout))

        def connect(self):
            pass

        def request(self, method, path, body, headers):
            calls.append(("request", method, path))

        def getresponse(self):
            return Response()

        def close(self):
            calls.append(("close",))

    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid")
    monkeypatch.setattr("jev_decision.client.http.client.HTTPSConnection", Connection)
    req = urllib.request.Request(DEFAULT_TYPESAFE_ENDPOINT, data=b"{}", method="POST")
    assert _http_transport(req, 1, 100) == (302, b"", {})
    assert calls == [
        ("connect", "api.typesafe.ai", 1), ("request", "POST", "/v1/systemone"), ("close",),
    ]


def test_construction_does_not_create_ledger(tmp_path):
    client = JevClient(api_key="", runtime=RuntimeConfig(home=tmp_path))
    assert not client.is_configured
    assert list(tmp_path.iterdir()) == []


def test_official_null_choice_descriptions(make_client):
    questions = {"q": {"type": "choice", "instructions": "Choose a category",
                        "criteria": {"yes": None, "no": None}}}
    assert normalize_questions(questions) == questions
    client, _ = make_client()
    result = client.evaluate("An excerpt", questions)
    assert result.status == "ok"
    assert result.get_choice("q").selected == "no"  # canonical sorted request


@pytest.mark.parametrize("hint", ["60", "Mon, 28 Sep 2099 12:00:00 GMT"])
def test_retry_hint_outside_deadline_prevents_extra_attempt(make_client, hint):
    client, ledger = make_client(lambda *_: (429, b"", {"Retry-After": hint}), timeout_s=0.2)
    result = client.evaluate("An excerpt", noul())
    assert result.error_code == "rate_limited"
    assert result.attempts == 1
    assert ledger.reservations == [1]


def test_retry_after_date_and_delta_parsing():
    from jev_decision.client import _retry_after
    assert _retry_after({"retry-after": "1.25"}) == 1.25
    assert _retry_after({"Retry-After": "Mon, 28 Sep 2020 12:00:00 GMT"}) == 0
    assert _retry_after({"Retry-After": "Mon, 28 Sep 2099 12:00:00 GMT"}) > 1
    for value in ["-1", "nan", "infinity", "not a date", "9" * 129]:
        assert _retry_after({"retry-after": value}) is None


def _noul_transport(calls):
    def transport(request, timeout_s, limit):
        body = json.loads(request.data)
        calls.append(body)
        answers = {key: {"type": "noul", "noul": 0.25} for key in body["questions"]}
        return 200, json.dumps({"model": body["model"], "answers": answers,
                                "usage": {"input_tokens": 12, "output_tokens": 0}}).encode()
    return transport


@pytest.mark.parametrize("via", ["argument", "environment"])
def test_library_key_before_setup_is_an_explicit_opt_in(monkeypatch, via):
    # README-level usage must work without `jev setup`; the default daily budget
    # and shared ledger still bound spend.
    calls = []
    if via == "environment":
        monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-library-key")
        client = JevClient(transport=_noul_transport(calls))
    else:
        client = JevClient(api_key="synthetic-library-key", transport=_noul_transport(calls))
    assert client.is_configured and client.runtime.enabled and not client.runtime.setup_complete
    result = client.evaluate("sample", {"q": {"type": "noul", "instructions": "Is this a sample?"}})
    assert result.status == "ok" and result.source == "provider" and len(calls) == 1
    assert client.runtime.ledger_path.exists() and not client.runtime.config_path.exists()
    assert client.runtime.daily_budget_usd == Decimal("1.00")


def test_saved_or_harness_runtime_is_not_upgraded_by_a_library_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-library-key")
    unused = lambda *_: pytest.fail("Disabled runtime reached the provider")  # noqa: E731
    # CLI/MCP pass their loaded runtime explicitly; a fresh install stays offline.
    harness = JevClient(runtime=RuntimeConfig.load(), transport=unused)
    assert harness.evaluate("sample", {"q": {"type": "noul", "instructions": "Is it?"}}).error_code == "runtime_disabled"
    # A saved configuration, including a disabled one, is always respected.
    RuntimeConfig(home=RuntimeConfig.load().home, daily_budget_usd=0).save()
    for client in (JevClient(transport=unused), JevClient(api_key="synthetic-library-key", transport=unused)):
        assert client.evaluate("sample", {"q": {"type": "noul", "instructions": "Is it?"}}).error_code == "runtime_disabled"
    monkeypatch.setenv("TYPESAFE_API_KEY", "${TYPESAFE_API_KEY}")
    assert not JevClient().is_configured


def test_plain_question_objects_match_the_mcp_and_typescript_form():
    plain = [{"id": "intent", "type": "choice", "instructions": "Classify the change.",
              "criteria": {"feature": "Adds behavior", "bug": "Fixes behavior", "unclear": None}},
             {"id": "legacy", "type": "score", "prompt": "How relevant?", "scale": ["Unrelated", "Related"]},
             NoulQuestion("typed", "Is this a sample?")]
    assert normalize_questions(plain) == {
        "intent": {"type": "choice", "instructions": "Classify the change.",
                   "criteria": {"feature": "Adds behavior", "bug": "Fixes behavior", "unclear": None}},
        "legacy": {"type": "score", "instructions": "How relevant?", "criteria": ["Unrelated", "Related"]},
        "typed": {"type": "noul", "instructions": "Is this a sample?"}}
    for bad in ([{"id": "x", "type": "noul", "instructions": "Q?", "unexpected": 1}],
                [{"id": "x", "type": "unknown", "instructions": "Q?"}],
                [{"type": "noul", "instructions": "Q?"}],
                [{"id": "x", "type": "noul", "instructions": "Q?"}, {"id": "x", "type": "noul", "instructions": "R?"}]):
        with pytest.raises(ValueError):
            normalize_questions(bad)
