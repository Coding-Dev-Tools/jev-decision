"""Crash-conservative, transactional daily accounting shared across harnesses."""

from __future__ import annotations

import calendar
import math
import sqlite3
import time as monotonic_time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from .runtime import RuntimeConfig

MAX_TOKENS_PER_ATTEMPT = 64_000
NANODOLLARS_PER_TOKEN = 42  # $0.042 per million input tokens; outputs are free.
RESERVATION_NANODOLLARS = MAX_TOKENS_PER_ATTEMPT * NANODOLLARS_PER_TOKEN
NANODOLLARS_PER_DOLLAR = 1_000_000_000


class BudgetError(RuntimeError):
    """Accounting unavailable; callers must not make an unreserved request."""


class BudgetExceeded(BudgetError):
    """The next worst-case attempt would exceed the daily shared cap."""


class BudgetDeadlineExceeded(BudgetError):
    """Accounting did not complete inside the caller's monotonic deadline."""


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    day: str
    reserved_usd: Decimal


def _zone(name: str = "America/New_York") -> Any:
    if name == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            return ZoneInfo(name)
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


def _local_day(instant: datetime, name: str = "America/New_York") -> date:
    if not isinstance(instant, datetime) or instant.tzinfo is None or instant.utcoffset() is None:
        raise BudgetError("Budget clock must return an aware datetime")
    instant = instant.astimezone(timezone.utc)
    zone = _zone() if name == "America/New_York" else _zone(name)
    if zone is not None:
        return instant.astimezone(zone).date()
    if name == "America/New_York":
        return (instant + _fallback_offset(instant)).date()
    raise BudgetError("Budget timezone data is unavailable")


def _next_reset(day: date, name: str = "America/New_York") -> str:
    tomorrow = day + timedelta(days=1)
    zone = _zone() if name == "America/New_York" else _zone(name)
    if zone is not None:
        result = datetime.combine(tomorrow, time.min, zone).astimezone(timezone.utc)
    elif name == "America/New_York":
        if tomorrow.year < 2007:
            raise BudgetError("Historical budget dates require installed IANA timezone data")
        # At midnight the spring switch has not yet happened; the fall day is still DST.
        daylight = _sunday(tomorrow.year, 3, 2) < tomorrow <= _sunday(tomorrow.year, 11, 1)
        result = datetime.combine(tomorrow, time(4 if daylight else 5), timezone.utc)
    else:
        raise BudgetError("Budget timezone data is unavailable")
    return result.isoformat()


def _bounds(instant: datetime, name: str) -> tuple[date, datetime, datetime]:
    day = _local_day(instant, name)
    start = datetime.fromisoformat(_next_reset(day - timedelta(days=1), name))
    end = datetime.fromisoformat(_next_reset(day, name))
    return day, start, end


def _check_deadline(deadline: Optional[float]) -> None:
    if deadline is not None:
        try:
            valid = type(deadline) in (int, float) and math.isfinite(deadline)
        except OverflowError:
            valid = False
        if not valid:
            raise BudgetError("Invalid accounting deadline")
    if deadline is not None and monotonic_time.monotonic() >= deadline:
        raise BudgetDeadlineExceeded("Budget accounting deadline exceeded")


def _dollars(value: int) -> Decimal:
    return Decimal(value) / NANODOLLARS_PER_DOLLAR


def _public_dollars(value: int) -> Any:
    amount = _dollars(value)
    number = float(amount)
    # Runtime permits any finite nonnegative cap; JSON must never contain Infinity.
    return number if math.isfinite(number) else str(amount)


class BudgetLedger:
    """One SQLite ledger per runtime home; every HTTP attempt needs a reservation."""

    def __init__(self, config: Optional[RuntimeConfig] = None, *,
                 clock: Optional[Callable[[], datetime]] = None,
                 deadline: Optional[float] = None):
        self.config = config or RuntimeConfig.load()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.limit = int(self.config.daily_budget_usd * NANODOLLARS_PER_DOLLAR)
        try:
            _check_deadline(deadline)
            self.config.home.mkdir(mode=0o700, parents=True, exist_ok=True)
            with closing(self._connect(deadline)) as connection:
                self._execute(connection, "PRAGMA journal_mode=WAL", deadline=deadline)
                self._execute(connection, "BEGIN IMMEDIATE", deadline=deadline)
                self._execute(connection, """CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id TEXT PRIMARY KEY,
                    day TEXT NOT NULL,
                    reserved_nano INTEGER NOT NULL CHECK(reserved_nano >= 0),
                    charged_nano INTEGER NOT NULL CHECK(charged_nano >= 0),
                    state TEXT NOT NULL CHECK(state IN ('reserved','unknown','settled')),
                    token_count INTEGER,
                    created_at_utc TEXT NOT NULL,
                    settled_at_utc TEXT
                )""", deadline=deadline)
                self._execute(connection, "CREATE INDEX IF NOT EXISTS reservations_day ON reservations(day)", deadline=deadline)
                self._execute(connection, "CREATE INDEX IF NOT EXISTS reservations_created ON reservations(created_at_utc)", deadline=deadline)
                # A timezone change never discards the current period's reservations.
                # Existing v1 ledgers have no window row and are migrated as New York.
                self._execute(connection, """CREATE TABLE IF NOT EXISTS budget_window (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    day TEXT NOT NULL, timezone TEXT NOT NULL,
                    starts_at_utc TEXT NOT NULL, resets_at_utc TEXT NOT NULL
                )""", deadline=deadline)
                self._execute(connection, "COMMIT", deadline=deadline)
                _check_deadline(deadline)
        except (OSError, sqlite3.Error):
            _check_deadline(deadline)
            raise BudgetError("Shared budget ledger is unavailable") from None

    def _connect(self, deadline: Optional[float] = None) -> sqlite3.Connection:
        _check_deadline(deadline)
        wait = 0.2 if deadline is None else max(0, min(0.2, deadline - monotonic_time.monotonic()))
        connection = sqlite3.connect(str(self.config.ledger_path), timeout=wait, isolation_level=None)
        try:
            if deadline is not None:
                connection.set_progress_handler(lambda: int(monotonic_time.monotonic() >= deadline), 100)
            self._execute(connection, "PRAGMA synchronous=FULL", deadline=deadline)
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _execute(connection: sqlite3.Connection, sql: str, parameters: tuple = (), *,
                 deadline: Optional[float] = None) -> sqlite3.Cursor:
        _check_deadline(deadline)
        remaining = 0.2 if deadline is None else max(0, min(0.2, deadline - monotonic_time.monotonic()))
        connection.execute("PRAGMA busy_timeout=" + str(int(remaining * 1000)))
        try:
            result = connection.execute(sql, parameters)
        except sqlite3.Error:
            if deadline is not None and deadline - monotonic_time.monotonic() <= 0.002:
                raise BudgetDeadlineExceeded("Budget accounting deadline exceeded") from None
            raise
        _check_deadline(deadline)
        return result

    def _window(self, connection: sqlite3.Connection, now: datetime, *,
                deadline: Optional[float] = None) -> tuple[str, str, str, str]:
        # Validate even when a stored period already exists (including clock rollback).
        _local_day(now, self.config.timezone)
        now = now.astimezone(timezone.utc)
        row = self._execute(connection, "SELECT day,timezone,starts_at_utc,resets_at_utc FROM budget_window WHERE singleton=1", deadline=deadline).fetchone()
        if row is not None:
            start, end = datetime.fromisoformat(row[2]), datetime.fromisoformat(row[3])
            if now < start:
                raise BudgetError("Budget clock precedes the active accounting period")
            if now < end:
                return row
            # Apply a changed timezone only after the previous reset. The transition
            # window starts no earlier than the old boundary, avoiding double spend.
            name = self.config.timezone
            day, start, next_end = _bounds(now, name)
            start = max(start, end)
        else:
            legacy = self._execute(connection, "SELECT 1 FROM reservations LIMIT 1", deadline=deadline).fetchone()
            name = "America/New_York" if legacy is not None else self.config.timezone
            day, start, next_end = _bounds(now, name)
        row = (day.isoformat(), name, start.isoformat(), next_end.isoformat())
        self._execute(connection, "INSERT OR REPLACE INTO budget_window VALUES (1,?,?,?,?)", row, deadline=deadline)
        return row

    def reserve(self, *, deadline: Optional[float] = None) -> Reservation:
        connection = None
        try:
            connection = self._connect(deadline)
            self._execute(connection, "BEGIN IMMEDIATE", deadline=deadline)
            now = self.clock()
            day, _, start, end = self._window(connection, now, deadline=deadline)
            used = self._execute(connection, "SELECT COALESCE(SUM(charged_nano),0) FROM reservations WHERE created_at_utc>=? AND created_at_utc<?", (start, end), deadline=deadline).fetchone()[0]
            if used + RESERVATION_NANODOLLARS > self.limit:
                raise BudgetExceeded("Shared daily Jev budget cannot fund another attempt")
            identifier = uuid.uuid4().hex
            self._execute(connection, "INSERT INTO reservations VALUES (?,?,?,?,?,?,?,?)",
                               (identifier, day, RESERVATION_NANODOLLARS, RESERVATION_NANODOLLARS,
                                "reserved", None, now.astimezone(timezone.utc).isoformat(), None), deadline=deadline)
            self._execute(connection, "COMMIT", deadline=deadline)
            return Reservation(identifier, day, _dollars(RESERVATION_NANODOLLARS))
        except sqlite3.Error:
            _check_deadline(deadline)
            raise BudgetError("Shared budget reservation is unavailable") from None
        finally:
            if connection is not None:
                connection.close()  # An uncommitted transaction rolls back.

    def settle(self, reservation: Reservation, token_count: Optional[int] = None, *,
               deadline: Optional[float] = None) -> None:
        if not isinstance(reservation, Reservation):
            raise BudgetError("Invalid budget reservation")
        if token_count is not None and (type(token_count) is not int or token_count < 0 or token_count > 100_000_000):
            raise BudgetError("Invalid provider token count; reservation remains held")
        connection = None
        try:
            connection = self._connect(deadline)
            self._execute(connection, "BEGIN IMMEDIATE", deadline=deadline)
            row = self._execute(connection, "SELECT day,state,token_count FROM reservations WHERE reservation_id=?",
                                (reservation.reservation_id,), deadline=deadline).fetchone()
            if row is None or row[0] != reservation.day:
                raise BudgetError("Unknown budget reservation")
            if row[1] == "settled":
                if token_count is not None and token_count != row[2]:
                    raise BudgetError("Conflicting provider usage settlement")
                self._execute(connection, "COMMIT", deadline=deadline)
                return
            state = "unknown" if token_count is None else "settled"
            charge = RESERVATION_NANODOLLARS if token_count is None else token_count * NANODOLLARS_PER_TOKEN
            # If provider usage exceeds its advertised bound, account for the actual
            # cost even above the cap; never hide an overspend by clamping it.
            now = self.clock()
            _local_day(now, self.config.timezone)
            self._execute(connection, "UPDATE reservations SET charged_nano=?,state=?,token_count=?,settled_at_utc=? WHERE reservation_id=?",
                          (charge, state, token_count, now.astimezone(timezone.utc).isoformat(), reservation.reservation_id), deadline=deadline)
            self._execute(connection, "COMMIT", deadline=deadline)
        except sqlite3.Error:
            _check_deadline(deadline)
            raise BudgetError("Shared budget settlement is unavailable; reservation remains held") from None
        finally:
            if connection is not None:
                connection.close()

    def status(self, *, deadline: Optional[float] = None) -> Dict[str, Any]:
        try:
            with closing(self._connect(deadline)) as connection:
                self._execute(connection, "BEGIN IMMEDIATE", deadline=deadline)
                day, name, start, end = self._window(connection, self.clock(), deadline=deadline)
                rows = self._execute(connection, "SELECT state,COUNT(*),COALESCE(SUM(charged_nano),0) FROM reservations WHERE created_at_utc>=? AND created_at_utc<? GROUP BY state",
                                     (start, end), deadline=deadline).fetchall()
                self._execute(connection, "COMMIT", deadline=deadline)
        except sqlite3.Error:
            _check_deadline(deadline)
            raise BudgetError("Shared budget status is unavailable") from None
        counts = {state: count for state, count, _ in rows}
        charges = {state: value for state, _, value in rows}
        committed = sum(charges.values())
        return {
            "day": day, "timezone": name, "configured_timezone": self.config.timezone,
            "starts_at": start, "resets_at": end,
            "daily_limit_usd": _public_dollars(self.limit),
            "committed_usd": _public_dollars(committed),
            "known_spend_usd": _public_dollars(charges.get("settled", 0)),
            "held_usd": _public_dollars(charges.get("reserved", 0) + charges.get("unknown", 0)),
            "remaining_usd": _public_dollars(max(0, self.limit - committed)),
            "attempts": sum(counts.values()), "pending_attempts": counts.get("reserved", 0),
            "unknown_attempts": counts.get("unknown", 0), "settled_attempts": counts.get("settled", 0),
            "max_tokens_per_attempt": MAX_TOKENS_PER_ATTEMPT,
            "reservation_usd": float(_dollars(RESERVATION_NANODOLLARS)),
            "rate_per_million_usd": 0.042,
            "accounting": "known usage plus worst-case holds; provider invoice unverified",
        }
