"""Market data for the leaders. Prices are integer cents, timestamps are epoch milliseconds (UTC).

A Bar's ts is the bar's END time (when its data became known), so a strategy that sees a bar at ts never uses
anything that happened later. Only the replay backend is enabled; live backends are stubs that make no network calls."""

from dataclasses import dataclass, field
from typing import Protocol

SYMBOLS = ("AAPL", "MSFT", "NVDA", "TSLA", "SPY")
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000}


class MarketDataError(Exception):
    pass


class NoDataError(MarketDataError):
    """The provider had nothing for the request. We never fill the gap with made-up prices."""


class NotEnabledError(MarketDataError):
    pass


@dataclass(frozen=True)
class Bar:
    symbol: str
    ts: int      # END of the bar, epoch ms UTC
    o: int
    h: int
    l: int
    c: int       # cents
    v: int


@dataclass
class BarSet:
    bars: list                       # sorted by (ts, symbol)
    provider: str
    symbols: list
    interval: str
    start: str                       # ISO dates requested
    end: str
    timezone: str                    # provider's native timezone, before conversion to UTC ms
    retrieved_at: str                # ISO UTC time the provider was asked
    from_cache: bool = False
    notes: list = field(default_factory=list)


class MarketData(Protocol):
    def get_bars(self, symbols, start, end, interval) -> BarSet: ...
