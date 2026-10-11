"""US equity market calendar (NYSE regular hours) in America/New_York.

Hand-entered holiday and early-close table for 2024-2027. A date outside that range raises CalendarRangeError instead of
guessing. Check the table against the exchange's published calendar before relying on it (see docs/LIMITATIONS.md)."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
OPEN, CLOSE, EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)
FIRST_YEAR, LAST_YEAR = 2024, 2027


class CalendarRangeError(ValueError):
    pass


def _d(*items):
    return frozenset(date.fromisoformat(s) for s in items)


HOLIDAYS = _d(
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27", "2024-06-19", "2024-07-04", "2024-09-02",
    "2024-11-28", "2024-12-25",
    # 2025 (includes the 2025-01-09 national day of mourning closure)
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26", "2025-06-19", "2025-07-04",
    "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026 (Jul 4 is a Saturday, observed Fri Jul 3)
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    # 2027 (Jun 19 Sat -> Fri Jun 18; Jul 4 Sun -> Mon Jul 5; Dec 25 Sat -> Fri Dec 24)
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24",
)
EARLY_CLOSES = _d(
    "2024-07-03", "2024-11-29", "2024-12-24",
    "2025-07-03", "2025-11-28", "2025-12-24",
    "2026-11-27", "2026-12-24",
    "2027-11-26",
)


def _check(d: date) -> None:
    if not FIRST_YEAR <= d.year <= LAST_YEAR:
        raise CalendarRangeError(f"calendar covers {FIRST_YEAR}-{LAST_YEAR}; {d} is outside it")


def is_trading_day(d: date) -> bool:
    _check(d)
    return d.weekday() < 5 and d not in HOLIDAYS


def is_early_close(d: date) -> bool:
    _check(d)
    return d in EARLY_CLOSES and is_trading_day(d)


def session(d: date):
    """(open, close) as tz-aware UTC datetimes, or None if the market is closed all day."""
    if not is_trading_day(d):
        return None
    close_t = EARLY_CLOSE if d in EARLY_CLOSES else CLOSE
    o = datetime.combine(d, OPEN, NY).astimezone(timezone.utc)
    c = datetime.combine(d, close_t, NY).astimezone(timezone.utc)
    return o, c


def ny_date(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).astimezone(NY).date()


def session_ms(d: date):
    s = session(d)
    return None if s is None else (int(s[0].timestamp() * 1000), int(s[1].timestamp() * 1000))


def in_session(ts_ms: int) -> bool:
    """True if ts falls in [open, close) of its New York trading day."""
    s = session_ms(ny_date(ts_ms))
    return s is not None and s[0] <= ts_ms < s[1]


def trading_days(start: date, end: date):
    d = start
    while d <= end:
        if is_trading_day(d):
            yield d
        d += timedelta(days=1)
