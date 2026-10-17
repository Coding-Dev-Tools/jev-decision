"""Portable timezones and conservative transitions without provider calls."""
import json
import sqlite3
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from jev_decision.budget import BudgetDeadlineExceeded, BudgetExceeded, BudgetLedger
from jev_decision.runtime import RuntimeConfig

CAP = Decimal("0.002688")


def config(home, zone="UTC", cap=CAP):
    return RuntimeConfig(home=home, timezone=zone, daily_budget_usd=cap, enabled=True)


def test_zero_cap_disables_admission_and_larger_caps_are_supported(tmp_path):
    with pytest.raises(BudgetExceeded):
        BudgetLedger(config(tmp_path / "zero", cap=Decimal(0))).reserve()
    ledger = BudgetLedger(config(tmp_path / "larger", cap=Decimal("12.50")))
    assert ledger.status()["daily_limit_usd"] == 12.5
    ledger.reserve()
    assert ledger.status()["attempts"] == 1


def test_finite_large_cap_does_not_emit_nonfinite_json(tmp_path):
    status = BudgetLedger(config(tmp_path, cap=Decimal("1e400"))).status()
    assert Decimal(str(status["daily_limit_usd"])) == Decimal("1e400")
    json.dumps(status, allow_nan=False)


def test_timezone_change_preserves_current_window_and_shared_cap(tmp_path):
    now = [datetime(2026, 9, 28, 0, 10, tzinfo=timezone.utc)]
    utc = BudgetLedger(config(tmp_path), clock=lambda: now[0])
    utc.reserve()
    changed = BudgetLedger(config(tmp_path, "America/New_York"), clock=lambda: now[0])
    with pytest.raises(BudgetExceeded):
        changed.reserve()
    status = changed.status()
    assert status["timezone"] == "UTC"
    assert status["configured_timezone"] == "America/New_York"
    assert status["resets_at"] == "2026-09-29T00:00:00+00:00"
    assert status["held_usd"] == float(CAP)
    now[0] = datetime(2026, 9, 29, 0, 1, tzinfo=timezone.utc)
    changed.reserve()
    status = changed.status()
    assert status["timezone"] == "America/New_York"
    assert status["starts_at"] == "2026-09-29T00:00:00+00:00"
    assert status["resets_at"] == "2026-09-29T04:00:00+00:00"
    with pytest.raises(BudgetExceeded):
        utc.reserve()  # A stale process cannot switch the period back and reset spend.


def test_legacy_new_york_rows_survive_utc_configuration_migration(tmp_path):
    cfg = config(tmp_path)
    db = sqlite3.connect(str(cfg.ledger_path))
    db.execute("""CREATE TABLE reservations (
        reservation_id TEXT PRIMARY KEY, day TEXT, reserved_nano INTEGER, charged_nano INTEGER,
        state TEXT, token_count INTEGER, created_at_utc TEXT, settled_at_utc TEXT)""")
    db.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?)",
               ("legacy", "2026-09-27", 2688000, 2688000, "reserved", None,
                "2026-09-28T03:30:00+00:00", None))
    db.commit()
    db.close()
    now = [datetime(2026, 9, 28, 3, 40, tzinfo=timezone.utc)]
    ledger = BudgetLedger(cfg, clock=lambda: now[0])
    with pytest.raises(BudgetExceeded):
        ledger.reserve()
    assert ledger.status()["timezone"] == "America/New_York"
    assert ledger.status()["pending_attempts"] == 1
    now[0] = datetime(2026, 9, 28, 4, 1, tzinfo=timezone.utc)
    ledger.reserve()
    status = ledger.status()
    assert status["timezone"] == "UTC"
    assert status["starts_at"] == "2026-09-28T04:00:00+00:00"
    assert status["attempts"] == 1
    with sqlite3.connect(str(cfg.ledger_path)) as db:
        assert db.execute("SELECT day,charged_nano FROM reservations WHERE reservation_id='legacy'").fetchone() == ("2026-09-27", 2688000)


def test_expired_accounting_deadline_creates_no_state(tmp_path):
    home = tmp_path / "absent"
    with pytest.raises(BudgetDeadlineExceeded):
        BudgetLedger(config(home), deadline=time.monotonic() - 1)
    assert not home.exists()


def test_clock_rollback_cannot_create_a_fresh_spending_window(tmp_path):
    from jev_decision.budget import BudgetError
    now = [datetime(2026, 9, 28, 12, tzinfo=timezone.utc)]
    ledger = BudgetLedger(config(tmp_path), clock=lambda: now[0])
    ledger.reserve()
    now[0] = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    with pytest.raises(BudgetError, match="precedes"):
        ledger.reserve()
