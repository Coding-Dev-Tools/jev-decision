"""Crash-conservative, transactional daily accounting shared across harnesses."""

from __future__ import annotations

import calendar
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from .runtime import RuntimeConfig

MAX_TOKENS_PER_ATTEMPT = 64_000
NANODOLLARS_PER_TOKEN = 42  # $0.042 per million total tokens.
RESERVATION_NANODOLLARS = MAX_TOKENS_PER_ATTEMPT * NANODOLLARS_PER_TOKEN
NANODOLLARS_PER_DOLLAR = 1_000_000_000


class BudgetError(RuntimeError):
    """Accounting unavailable; callers must not make an unreserved request."""


class BudgetExceeded(BudgetError):
    """The next worst-case attempt would exceed the daily shared cap."""


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    day: str
    reserved_usd: Decimal


def _zone() -> Any:
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            return ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            return None
    except ImportError:
        return None


def _sunday(year: int, month: int, ordinal: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(calendar.SUNDAY - first.weekday()) % 7 + (ordinal - 1) * 7)


def _fallback_offset(instant: datetime) -> timedelta:
    # Current US law, effective 2007. Windows may have no IANA tzdata package.
    # Refuse older timestamps instead of silently using modern rules for history.
    if instant.year < 2007:
        raise BudgetError("Historical budget dates require installed IANA timezone data")
    start = datetime.combine(_sunday(instant.year, 3, 2), time(7), timezone.utc)
    end = datetime.combine(_sunday(instant.year, 11, 1), time(6), timezone.utc)
    return timedelta(hours=-4 if start <= instant < end else -5)


def _local_day(instant: datetime) -> date:
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise BudgetError("Budget clock must return an aware datetime")
    instant = instant.astimezone(timezone.utc)
    zone = _zone()
    if zone is not None:
        return instant.astimezone(zone).date()
    return (instant + _fallback_offset(instant)).date()


def _next_reset(day: date) -> str:
    tomorrow = day + timedelta(days=1)
    zone = _zone()
    if zone is not None:
        result = datetime.combine(tomorrow, time.min, zone).astimezone(timezone.utc)
    else:
        if tomorrow.year < 2007:
            raise BudgetError("Historical budget dates require installed IANA timezone data")
        # At midnight the spring switch has not yet happened; the fall day is still DST.
        daylight = _sunday(tomorrow.year, 3, 2) < tomorrow <= _sunday(tomorrow.year, 11, 1)
        result = datetime.combine(tomorrow, time(4 if daylight else 5), timezone.utc)
    return result.isoformat()


def _dollars(value: int) -> Decimal:
    return Decimal(value) / NANODOLLARS_PER_DOLLAR


class BudgetLedger:
    """One SQLite ledger per runtime home; every HTTP attempt needs a reservation."""

    def __init__(self, config: Optional[RuntimeConfig] = None, *, clock: Optional[Callable[[], datetime]] = None):
        self.config = config or RuntimeConfig.load()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.limit = int(self.config.daily_budget_usd * NANODOLLARS_PER_DOLLAR)
        try:
            self.config.home.mkdir(mode=0o700, parents=True, exist_ok=True)
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("""CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id TEXT PRIMARY KEY,
                    day TEXT NOT NULL,
                    reserved_nano INTEGER NOT NULL CHECK(reserved_nano >= 0),
                    charged_nano INTEGER NOT NULL CHECK(charged_nano >= 0),
                    state TEXT NOT NULL CHECK(state IN ('reserved','unknown','settled')),
                    token_count INTEGER,
                    created_at_utc TEXT NOT NULL,
                    settled_at_utc TEXT
                )""")
                connection.execute("CREATE INDEX IF NOT EXISTS reservations_day ON reservations(day)")
        except (OSError, sqlite3.Error):
            raise BudgetError("Shared budget ledger is unavailable") from None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.config.ledger_path), timeout=0.2, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=200")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def reserve(self) -> Reservation:
        connection = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            now = self.clock()
            day = _local_day(now).isoformat()
            used = connection.execute("SELECT COALESCE(SUM(charged_nano),0) FROM reservations WHERE day=?", (day,)).fetchone()[0]
            if used + RESERVATION_NANODOLLARS > self.limit:
                raise BudgetExceeded("Shared daily Jev budget cannot fund another attempt")
            identifier = uuid.uuid4().hex
            connection.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?)",
                               (identifier, day, RESERVATION_NANODOLLARS, RESERVATION_NANODOLLARS,
                                "reserved", None, now.astimezone(timezone.utc).isoformat(), None))
            connection.commit()
            return Reservation(identifier, day, _dollars(RESERVATION_NANODOLLARS))
        except sqlite3.Error:
            raise BudgetError("Shared budget reservation is unavailable") from None
        finally:
            if connection is not None:
                connection.close()  # An uncommitted transaction rolls back.

    def settle(self, reservation: Reservation, token_count: Optional[int] = None) -> None:
        if not isinstance(reservation, Reservation):
            raise BudgetError("Invalid budget reservation")
        if token_count is not None and (type(token_count) is not int or token_count < 0 or token_count > 100_000_000):
            raise BudgetError("Invalid provider token count; reservation remains held")
        connection = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT day,state,token_count FROM reservations WHERE reservation_id=?",
                                     (reservation.reservation_id,)).fetchone()
            if row is None or row[0] != reservation.day:
                raise BudgetError("Unknown budget reservation")
            if row[1] == "settled":
                if token_count is not None and token_count != row[2]:
                    raise BudgetError("Conflicting provider usage settlement")
                connection.commit()
                return
            state = "unknown" if token_count is None else "settled"
            charge = RESERVATION_NANODOLLARS if token_count is None else token_count * NANODOLLARS_PER_TOKEN
            # If provider usage exceeds its advertised bound, account for the actual
            # cost even above the cap; never hide an overspend by clamping it.
            now = self.clock()
            _local_day(now)
            connection.execute("UPDATE reservations SET charged_nano=?,state=?,token_count=?,settled_at_utc=? WHERE reservation_id=?",
                               (charge, state, token_count, now.astimezone(timezone.utc).isoformat(), reservation.reservation_id))
            connection.commit()
        except sqlite3.Error:
            raise BudgetError("Shared budget settlement is unavailable; reservation remains held") from None
        finally:
            if connection is not None:
                connection.close()

    def status(self) -> Dict[str, Any]:
        day = _local_day(self.clock())
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute("SELECT state,COUNT(*),COALESCE(SUM(charged_nano),0) FROM reservations WHERE day=? GROUP BY state",
                                          (day.isoformat(),)).fetchall()
        except sqlite3.Error:
            raise BudgetError("Shared budget status is unavailable") from None
        counts = {state: count for state, count, _ in rows}
        charges = {state: value for state, _, value in rows}
        committed = sum(charges.values())
        return {
            "day": day.isoformat(), "timezone": "America/New_York", "resets_at": _next_reset(day),
            "daily_limit_usd": float(_dollars(self.limit)),
            "committed_usd": float(_dollars(committed)),
            "known_spend_usd": float(_dollars(charges.get("settled", 0))),
            "held_usd": float(_dollars(charges.get("reserved", 0) + charges.get("unknown", 0))),
            "remaining_usd": float(_dollars(max(0, self.limit - committed))),
            "attempts": sum(counts.values()), "pending_attempts": counts.get("reserved", 0),
            "unknown_attempts": counts.get("unknown", 0), "settled_attempts": counts.get("settled", 0),
            "max_tokens_per_attempt": MAX_TOKENS_PER_ATTEMPT,
            "reservation_usd": float(_dollars(RESERVATION_NANODOLLARS)),
            "rate_per_million_usd": 0.042,
            "accounting": "known usage plus worst-case holds; provider invoice unverified",
        }
