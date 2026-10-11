"""Replay backend: fetch historical bars once, cache them to disk, and serve the cached copy on every later request.
No data means NoDataError; nothing is ever interpolated or invented."""

import hashlib
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import orjson

from marketdata import INTERVAL_MS, SYMBOLS, Bar, BarSet, MarketDataError, NoDataError
from marketdata.calendar import in_session, ny_date

CACHE_VERSION = 1


def _iso(x: date) -> str:
    return x.isoformat()


class ReplayBackend:
    name = "replay"

    def __init__(self, provider, cache_dir="marketdata/cache", now=None):
        self.provider, self.cache_dir, self._now = provider, Path(cache_dir), now

    def _key(self, symbols, start, end, interval):
        raw = orjson.dumps([CACHE_VERSION, self.provider.name, symbols, interval, _iso(start), _iso(end)])
        return hashlib.sha256(raw).hexdigest()[:20]

    def get_bars(self, symbols, start: date, end: date, interval: str) -> BarSet:
        """start/end are inclusive New York dates."""
        symbols = sorted(set(symbols))
        bad = [s for s in symbols if s not in SYMBOLS]
        if bad or not symbols:
            raise MarketDataError(f"symbols must be from {', '.join(SYMBOLS)}; got {', '.join(bad) or 'none'}")
        if interval not in INTERVAL_MS:
            raise MarketDataError(f"interval must be one of {', '.join(INTERVAL_MS)}")
        if end < start:
            raise MarketDataError("end is before start")
        path = self.cache_dir / f"{self.provider.name}-{self._key(symbols, start, end, interval)}.json"
        cached = self._read_cache(path)
        if cached is not None:
            return cached
        rows, tz = self.provider.fetch(symbols, start, end + timedelta(days=1), interval)
        retrieved = (self._now() if self._now else datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
        width = INTERVAL_MS[interval]
        bars, dropped, seen = [], 0, set()
        for sym, t0, o, h, l, c, v in rows:
            ts = t0 + width      # bar END
            if sym not in symbols or not (start <= ny_date(t0) <= end) or not in_session(t0):
                dropped += 1
                continue
            if min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h) or v < 0 or (sym, ts) in seen:
                dropped += 1
                continue
            seen.add((sym, ts))
            bars.append(Bar(sym, ts, o, h, l, c, v))
        if not bars:
            raise NoDataError(f"{self.provider.name} returned no regular-session bars for {','.join(symbols)} "
                              f"{interval} {start}..{end}; nothing was fabricated")
        bars.sort(key=lambda b: (b.ts, b.symbol))
        notes = [f"dropped {dropped} rows outside regular hours/range or failing sanity checks"] if dropped else []
        missing = [s for s in symbols if not any(b.symbol == s for b in bars)]
        if missing:
            notes.append(f"no data for {', '.join(missing)}")
        bs = BarSet(bars, self.provider.name, symbols, interval, _iso(start), _iso(end), tz, retrieved, False, notes)
        self._write_cache(path, bs)
        return bs

    def _read_cache(self, path):
        try:
            d = orjson.loads(path.read_bytes())
            if d["version"] != CACHE_VERSION:
                return None
            m = d["meta"]
            bars = [Bar(*b) for b in d["bars"]]
            return BarSet(bars, m["provider"], m["symbols"], m["interval"], m["start"], m["end"], m["timezone"],
                          m["retrieved_at"], True, list(m.get("notes", [])) + [f"served from cache {path.name}"])
        except (OSError, KeyError, TypeError, ValueError):
            return None  # missing or unreadable cache: fetch again

    def _write_cache(self, path, bs):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        meta = {"provider": bs.provider, "symbols": bs.symbols, "interval": bs.interval, "start": bs.start, "end": bs.end,
                "timezone": bs.timezone, "retrieved_at": bs.retrieved_at, "notes": bs.notes}
        body = {"version": CACHE_VERSION, "meta": meta,
                "bars": [[b.symbol, b.ts, b.o, b.h, b.l, b.c, b.v] for b in bs.bars]}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(orjson.dumps(body))
        os.replace(tmp, path)
