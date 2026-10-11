"""Feeds bars to strategies in time order and turns their output into numbered signals. Replay is deterministic: no wall
clock, no randomness. leader_ts is the decision time, which in replay equals the bar's end time (data_ts)."""

from leaders.signal import Signal
from leaders.strategies import STRATEGIES


class LookAheadError(RuntimeError):
    pass


def generate(bars, strategy: str, data_source: str, qty: int = 10, **params):
    """`bars` may be any iterable (a lazy one is consumed one item at a time). Yields Signal objects.
    Bars must be non-decreasing in ts; a bar older than one already seen raises LookAheadError."""
    cls = STRATEGIES[strategy]
    per_symbol, seq, last_ts = {}, 0, None
    for bar in bars:
        if last_ts is not None and bar.ts < last_ts:
            raise LookAheadError(f"bars out of order: {bar.ts} after {last_ts}")
        last_ts = bar.ts
        st = per_symbol.get(bar.symbol)
        if st is None:
            st = per_symbol[bar.symbol] = cls(bar.symbol, qty=qty, **params)
        out = st.on_bar(bar)
        if out:
            seq += 1
            side, q, px = out
            yield Signal(seq, bar.symbol, side, q, px, bar.ts, data_source, bar.ts)
