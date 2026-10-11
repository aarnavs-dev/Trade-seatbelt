"""Signal format v1: a JSON memo with exactly these keys and nothing else. See docs/SIGNALS.md.
Money is integer cents, times are epoch milliseconds UTC, qty is an integer number of shares."""

import re
from dataclasses import dataclass

import orjson

from marketdata import SYMBOLS

KEYS = ("v", "leader_seq", "symbol", "side", "qty", "ref_price", "data_ts", "data_source", "leader_ts")
MAX_MEMO_BYTES = 400
MAX_QTY = 1_000_000
SOURCE_RE = re.compile(r"^[a-z0-9\-]{1,16}$")


class SignalError(ValueError):
    """Carries the human-readable reason a memo/signal was rejected."""


@dataclass(frozen=True)
class Signal:
    leader_seq: int
    symbol: str
    side: str
    qty: int
    ref_price: int
    data_ts: int
    data_source: str
    leader_ts: int
    v: int = 1

    def to_dict(self):
        return {k: getattr(self, k) for k in KEYS}

    def to_memo(self) -> bytes:
        return orjson.dumps(self.to_dict(), option=orjson.OPT_SORT_KEYS)


def _int(d, k, lo, hi):
    x = d[k]
    if type(x) is not int or not lo <= x <= hi:
        raise SignalError(f"{k} must be an integer in [{lo}, {hi}]")
    return x


def validate(d) -> Signal:
    if not isinstance(d, dict):
        raise SignalError("memo is not a JSON object")
    if set(d) != set(KEYS):
        extra, missing = sorted(set(d) - set(KEYS)), sorted(set(KEYS) - set(d))
        raise SignalError("unexpected keys " + ",".join(extra) if extra else "missing keys " + ",".join(missing))
    if d["v"] != 1 or type(d["v"]) is not int:
        raise SignalError(f"unsupported version {d['v']!r}")
    if d["symbol"] not in SYMBOLS:
        raise SignalError("symbol not in the supported list")
    if d["side"] not in ("BUY", "SELL"):
        raise SignalError("side must be BUY or SELL")
    src = d["data_source"]
    if not isinstance(src, str) or not SOURCE_RE.match(src):
        raise SignalError("data_source must match [a-z0-9-]{1,16}")
    return Signal(leader_seq=_int(d, "leader_seq", 1, 2**53), symbol=d["symbol"], side=d["side"],
                  qty=_int(d, "qty", 1, MAX_QTY), ref_price=_int(d, "ref_price", 1, 10**9),
                  data_ts=_int(d, "data_ts", 0, 2**53), data_source=src, leader_ts=_int(d, "leader_ts", 0, 2**53))


def parse_memo(raw) -> Signal:
    if isinstance(raw, str):
        raw = raw.encode()
    if len(raw) > MAX_MEMO_BYTES:
        raise SignalError(f"memo is {len(raw)} bytes, over the {MAX_MEMO_BYTES}-byte limit")
    try:
        d = orjson.loads(raw)
    except orjson.JSONDecodeError:
        raise SignalError("memo is not valid JSON") from None
    return validate(d)
