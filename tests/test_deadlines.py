"""No-network regression tests for cancellation and a single accounting deadline."""
import http.client
import json
import sqlite3
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from jev_decision.budget import BudgetDeadlineExceeded, BudgetLedger
from jev_decision.client import (
    DEFAULT_TYPESAFE_ENDPOINT,
    JevClient,
    _bounded_transport,
    _http_transport,
)
from jev_decision.runtime import RuntimeConfig

QUESTIONS = {"q": {"type": "noul", "instructions": "Is the evidence relevant?"}}
BODY = json.dumps({"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.8}},
                   "usage": {"input_tokens": 10, "output_tokens": 1}}).encode()


def test_late_connect_never_sends_after_caller_timeout(monkeypatch):
    release = threading.Event()
    connected = threading.Event()
    sends = []

    class FakeSocket:
        def sendall(self, data):
            sends.append(data)
        def close(self):
            pass
        def shutdown(self, *_):
            pass
        def settimeout(self, *_):
            pass

    class SlowConnect(http.client.HTTPConnection):
        def __init__(self, host, timeout):
            super().__init__(host, 443, timeout=timeout)
        def connect(self):
            release.wait(1)
            self.sock = FakeSocket()
            connected.set()

    monkeypatch.setattr("jev_decision.client.http.client.HTTPSConnection", SlowConnect)
    request = urllib.request.Request(DEFAULT_TYPESAFE_ENDPOINT, data=b"{}", method="POST")
    try:
        with pytest.raises(TimeoutError):
            _bounded_transport(_http_transport, request, 0.03, 100)
        release.set()
        assert connected.wait(1)
        time.sleep(0.03)
        assert sends == []
    finally:
        release.set()


def test_slow_injected_settlement_cannot_return_or_cache_success(tmp_path):
    released = threading.Event()
    entered = threading.Event()
    calls = []
    class SlowLedger:
        def reserve(self):
            return len(calls)
        def settle(self, *_args, **_kwargs):
            entered.set()
            released.wait(5)
    def transport(*_):
        calls.append(1)
        return 200, BODY
    client = JevClient(api_key="fake-offline-test-key", runtime=RuntimeConfig(home=tmp_path, enabled=True),
                       budget_ledger=SlowLedger(), transport=transport, timeout_s=0.5)
    try:
        started = time.monotonic()
        result = client.evaluate("An excerpt", QUESTIONS)
        assert time.monotonic() - started < 1.5
        assert entered.is_set()
        assert result.error_code == "timeout"
        assert result.decisions == {}
        assert result.status == "unavailable"
        released.set()
        client.timeout_s = 2  # Recovery tests cache behavior, not a tiny I/O deadline.
        assert client.evaluate("An excerpt", QUESTIONS).source == "provider"
        assert len(calls) == 2
    finally:
        released.set()


@pytest.mark.parametrize("lock_at", ["reserve", "settle"])
def test_sqlite_contention_bounds_client_and_keeps_holds(tmp_path, lock_at):
    config = RuntimeConfig(home=tmp_path, enabled=True)
    ledger = BudgetLedger(config)
    blocker = sqlite3.connect(str(config.ledger_path), isolation_level=None, check_same_thread=False)
    calls = []
    def transport(*_):
        calls.append(1)
        if lock_at == "settle":
            blocker.execute("BEGIN IMMEDIATE")
        return 200, BODY
    if lock_at == "reserve":
        blocker.execute("BEGIN IMMEDIATE")
    client = JevClient(api_key="fake-offline-test-key", runtime=config, budget_ledger=ledger,
                       transport=transport, timeout_s=0.5)
    try:
        started = time.monotonic()
        result = client.evaluate("An excerpt", QUESTIONS)
        assert time.monotonic() - started < 1.5
        # SQLite may exhaust its shorter busy timeout before the whole request.
        assert result.error_code in {"timeout", "budget_unavailable"}
        assert not result.decisions
        assert len(calls) == (1 if lock_at == "settle" else 0)
    finally:
        blocker.close()
    status = ledger.status()
    assert status["pending_attempts"] == (1 if lock_at == "settle" else 0)
    assert status["known_spend_usd"] == 0
    if lock_at == "settle":
        assert status["held_usd"] == 0.002688


@pytest.mark.parametrize("lock_at", ["reserve", "settle"])
def test_sqlite_operations_respect_deadline_shorter_than_busy_timeout(tmp_path, lock_at):
    config = RuntimeConfig(home=tmp_path, enabled=True)
    ledger = BudgetLedger(config)
    reservation = ledger.reserve() if lock_at == "settle" else None
    blocker = sqlite3.connect(str(config.ledger_path), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        deadline = started + 0.05
        with pytest.raises(BudgetDeadlineExceeded):
            if lock_at == "reserve":
                ledger.reserve(deadline=deadline)
            else:
                ledger.settle(reservation, token_count=11, deadline=deadline)
        assert time.monotonic() - started < 0.5
    finally:
        blocker.close()
    assert ledger.status()["pending_attempts"] == (1 if lock_at == "settle" else 0)


def test_external_deadline_cannot_extend_client_or_trigger_expired_work(tmp_path):
    calls = []
    client = JevClient(api_key="fake-offline-test-key", runtime=RuntimeConfig(home=tmp_path, enabled=True),
                       transport=lambda *_: calls.append(1))
    result = client.evaluate("An excerpt", QUESTIONS, deadline_monotonic=time.monotonic() - 1)
    assert result.error_code == "timeout"
    assert calls == []
    assert not (tmp_path / "budget.sqlite3").exists()
    for invalid in (float("nan"), float("inf"), True):
        assert client.evaluate("An excerpt", QUESTIONS, deadline_monotonic=invalid).error_code == "invalid_request"


def test_preparation_uses_the_same_deadline_and_cannot_send_later(tmp_path, monkeypatch):
    from jev_decision import policy
    release = threading.Event()
    calls = []
    original = policy.sanitize_state
    def delayed(*args, **kwargs):
        release.wait(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(policy, "sanitize_state", delayed)
    client = JevClient(api_key="fake-offline-test-key", runtime=RuntimeConfig(home=tmp_path, enabled=True),
                       transport=lambda *_: calls.append(1), timeout_s=0.03)
    try:
        started = time.monotonic()
        result = client.evaluate("An excerpt", QUESTIONS)
        assert time.monotonic() - started < 0.25
        assert result.error_code == "timeout"
        release.set()
        time.sleep(0.02)
        assert calls == []
        assert not (tmp_path / "budget.sqlite3").exists()
    finally:
        release.set()


def test_concurrent_calls_share_budget_and_cannot_race_one_attempt_cap(tmp_path):
    config = RuntimeConfig(home=tmp_path, enabled=True, daily_budget_usd=Decimal("0.002688"))
    ledger = BudgetLedger(config)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def transport(*_):
        calls.append(1)
        entered.set()
        assert release.wait(1)
        return 200, BODY
    client = JevClient(api_key="fake-offline-test-key", runtime=config, budget_ledger=ledger, transport=transport)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.evaluate, "First excerpt", QUESTIONS)
        try:
            assert entered.wait(1)
            second = pool.submit(client.evaluate, "Second excerpt", QUESTIONS).result(timeout=1)
            assert second.error_code == "budget_exhausted"
            assert calls == [1]
        finally:
            release.set()
        assert first.result(timeout=1).status == "ok"
    assert ledger.status()["attempts"] == 1


def test_response_validation_is_bounded_and_preserves_uncertain_hold(tmp_path, monkeypatch):
    import jev_decision.client as client_module
    release = threading.Event()
    entered = threading.Event()
    original = client_module.validate_response
    def delayed(*args):
        entered.set()
        release.wait(5)
        return original(*args)
    monkeypatch.setattr(client_module, "validate_response", delayed)
    config = RuntimeConfig(home=tmp_path, enabled=True)
    ledger = BudgetLedger(config)
    client = JevClient(api_key="fake-offline-test-key", runtime=config, budget_ledger=ledger,
                       transport=lambda *_: (200, BODY), timeout_s=0.5)
    try:
        started = time.monotonic()
        result = client.evaluate("An excerpt", QUESTIONS)
        assert time.monotonic() - started < 1.5
        assert entered.is_set()
        assert result.error_code == "timeout"
        assert result.decisions == {}
        assert ledger.status()["pending_attempts"] == 1
        release.set()
        client.timeout_s = 2  # Leave CI filesystem time for the uncached recovery.
        assert client.evaluate("An excerpt", QUESTIONS).source == "provider"
    finally:
        release.set()


def test_two_concurrent_windows_share_one_client_without_changing_its_timeout(tmp_path):
    barrier = threading.Barrier(2)
    def transport(*_):
        barrier.wait(timeout=1)
        return 200, BODY
    client = JevClient(api_key="fake-offline-test-key", runtime=RuntimeConfig(home=tmp_path, enabled=True), transport=transport)
    deadline = time.monotonic() + 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.evaluate, text, QUESTIONS, deadline_monotonic=deadline)
                   for text in ("First excerpt", "Second excerpt")]
        results = [f.result(timeout=2) for f in futures]
        assert [r.status for r in results] == ["ok", "ok"], [r.to_dict() for r in results]
    assert client.timeout_s == 5
    assert BudgetLedger(client.runtime).status()["attempts"] == 2
