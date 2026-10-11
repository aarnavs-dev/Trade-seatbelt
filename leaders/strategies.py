"""Leader strategies. Each is a small state machine fed ONE bar at a time through on_bar(); it has no handle on the
dataset, so it can only use bars with ts <= the bar being decided (checked by tests/test_leaders.py). Integer math only.
Long-only: BUY opens, SELL closes. Qty is a fixed number of shares."""

from collections import deque

from marketdata import Bar
from marketdata.calendar import NY, ny_date, session_ms



class Strategy:
    name = "base"

    def __init__(self, symbol, qty=10):
        self.symbol, self.qty = symbol, qty
        self.long = False

    def _out(self, side, bar):
        self.long = side == "BUY"
        return (side, self.qty, bar.c)

    def on_bar(self, bar: Bar):
        """Returns None or (side, qty, ref_price_cents)."""
        raise NotImplementedError


class OpeningRangeBreakout(Strategy):
    """First `range_ms` of the session sets the high/low. After it, the first close above the high buys; a close below
    the range low, or the last bar of the session, sells. One entry per day."""
    name = "orb"

    def __init__(self, symbol, qty=10, range_minutes=30):
        super().__init__(symbol, qty)
        self.range_ms, self.day = range_minutes * 60_000, None

    def on_bar(self, bar):
        d = ny_date(bar.ts - 1)  # the bar's own trading day (ts is its end)
        sess = session_ms(d)
        if sess is None:
            return None
        if d != self.day:
            self.day, self.hi, self.lo, self.entered = d, None, None, False
            self.long = False
        open_ms, close_ms = sess
        if bar.ts <= open_ms + self.range_ms:
            self.hi = bar.h if self.hi is None else max(self.hi, bar.h)
            self.lo = bar.l if self.lo is None else min(self.lo, bar.l)
            return None
        if self.hi is None:
            return None
        if not self.long and not self.entered and bar.c > self.hi and bar.ts < close_ms:
            self.entered = True
            return self._out("BUY", bar)
        if self.long and (bar.c < self.lo or bar.ts >= close_ms):
            return self._out("SELL", bar)
        return None


class Momentum(Strategy):
    """Buy when the close is up at least `up_bps` basis points over the last `lookback` bars; sell when it is down
    at least `down_bps` over that window."""
    name = "momentum"

    def __init__(self, symbol, qty=10, lookback=12, up_bps=30, down_bps=15):
        super().__init__(symbol, qty)
        self.closes, self.lookback, self.up, self.down = deque(maxlen=lookback + 1), lookback, up_bps, down_bps

    def on_bar(self, bar):
        self.closes.append(bar.c)
        if len(self.closes) <= self.lookback:
            return None
        base = self.closes[0]
        change = (bar.c - base) * 10_000  # compare change/base to bps without division
        if not self.long and change >= self.up * base:
            return self._out("BUY", bar)
        if self.long and change <= -self.down * base:
            return self._out("SELL", bar)
        return None


class MeanReversion(Strategy):
    """Buy when the close is `dip_bps` below the simple moving average of the last `window` bars; sell when it is back
    at or above the average."""
    name = "meanrev"

    def __init__(self, symbol, qty=10, window=20, dip_bps=40):
        super().__init__(symbol, qty)
        self.closes, self.window, self.dip = deque(maxlen=window), window, dip_bps

    def on_bar(self, bar):
        self.closes.append(bar.c)
        if len(self.closes) < self.window:
            return None
        total = sum(self.closes)  # compare c*n against sum to avoid dividing
        n = self.window
        if not self.long and bar.c * n * 10_000 <= total * (10_000 - self.dip):
            return self._out("BUY", bar)
        if self.long and bar.c * n >= total:
            return self._out("SELL", bar)
        return None


STRATEGIES = {"orb": OpeningRangeBreakout, "momentum": Momentum, "meanrev": MeanReversion}
