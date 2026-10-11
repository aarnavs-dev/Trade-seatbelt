"""Replay data for a follow run: real historical bars when they can be fetched, else the committed SYNTHETIC fixture.
The two are never mixed and the label travels with the data into the manifest and the printed output.

Price-band anchor = the LAST BAR's close of the previous session (5-minute bars), per symbol. It is not the official
closing price (no closing auction, IEX/Yahoo bars)."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import orjson

from marketdata import SYMBOLS, Bar, BarSet
from marketdata.calendar import NY, is_trading_day, ny_date
from marketdata.providers import choose_provider
from marketdata.replay import ReplayBackend

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic_bars" / "bars_5m.json"
INTERVAL = "5m"
LABEL_REAL = "REPLAY of real historical data"
LABEL_SYNTH = "SYNTHETIC FIXTURE"
ANCHOR_NOTE = "previous session's last-bar close (5m bars), not an official closing price"


@dataclass
class ReplayData:
    bars: list              # the replayed session's bars, sorted by (ts, symbol)
    anchors: dict           # symbol -> cents
    provider: str
    synthetic: bool
    label: str
    session: str            # ISO date replayed
    prev_session: str
    fallback_reason: str | None = None

    def describe(self) -> dict:
        return {"label": self.label, "synthetic": self.synthetic, "provider": self.provider, "session": self.session,
                "prev_session": self.prev_session, "interval": INTERVAL, "anchor": ANCHOR_NOTE,
                "fallback_reason": self.fallback_reason, "symbols": sorted(self.anchors)}


def _bar_day(b: Bar) -> date:
    return ny_date(b.ts - 1)    # ts is the bar's END; the last bar of a day ends exactly at the close


def split(bars, prev: date, day: date, symbols):
    """-> (replayed session's bars, anchors). Raises ValueError unless every wanted symbol has both sessions."""
    today = sorted((b for b in bars if _bar_day(b) == day and b.symbol in symbols), key=lambda b: (b.ts, b.symbol))
    anchors = {}
    for s in symbols:
        last = [b for b in bars if b.symbol == s and _bar_day(b) == prev]
        if not last or not any(b.symbol == s for b in today):
            raise ValueError(f"no bars for {s} on both {prev} and {day}")
        anchors[s] = max(last, key=lambda b: b.ts).c
    return today, anchors


def load_fixture(symbols=SYMBOLS, path=FIXTURE, reason=None) -> ReplayData:
    d = orjson.loads(Path(path).read_bytes())
    bars = [Bar(*b) for b in d["bars"]]
    prev, day = date.fromisoformat(d["meta"]["start"]), date.fromisoformat(d["meta"]["end"])
    symbols = [s for s in symbols if s in d["meta"]["symbols"]]
    today, anchors = split(bars, prev, day, symbols)
    return ReplayData(today, anchors, "synthetic", True, LABEL_SYNTH, day.isoformat(), prev.isoformat(), reason)


def last_sessions(today: date):
    """(previous trading day, last COMPLETED trading day before `today`)."""
    d, found = today - timedelta(days=1), []
    while len(found) < 2:
        if is_trading_day(d):
            found.append(d)
        d -= timedelta(days=1)
    return found[1], found[0]


def previous_trading_day(day: date) -> date:
    d = day - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def load(symbols=SYMBOLS, *, offline=False, today=None, day=None, cache_dir="marketdata/cache", dotenv=".env", provider=None) -> ReplayData:
    """Real data if possible; any failure (no network, no keys, rate limit, no bars for a symbol) -> the fixture, with the
    reason recorded. Never raises for a data problem. `day` pins the replayed session (a chain feed's signals say which)."""
    if offline:
        return load_fixture(symbols, reason="offline requested")
    try:
        if day is not None:
            prev = previous_trading_day(day)
        else:
            prev, day = last_sessions(today or datetime.now(NY).date())
        backend = ReplayBackend(provider or choose_provider(dotenv_path=dotenv), cache_dir=cache_dir)
        bs: BarSet = backend.get_bars(list(symbols), prev, day, INTERVAL)
        bars, anchors = split(bs.bars, prev, day, list(symbols))
        return ReplayData(bars, anchors, bs.provider, False, LABEL_REAL, day.isoformat(), prev.isoformat())
    except Exception as e:      # noqa: BLE001  any failure of a network provider means "use the fixture", and we say why
        return load_fixture(symbols, reason=f"{type(e).__name__}: {str(e)[:160]}")
