"""The follower. Pre-filters run in a FIXED order, before the gate:

  INVALID_SIGNAL, UNTRUSTED_LEADER, DUPLICATE_SIGNAL, UNKNOWN_SYMBOL, MARKET_CLOSED, STALE_SIGNAL, FEED_GAP,
  LEADER_DRAWDOWN_STOP, SELL_WITHOUT_POSITION

then the leader's qty is scaled (SIZE_ROUNDS_TO_ZERO), a SELL is clipped to the follower's long position unless
allow_short (the clip is recorded in copier.log as SIZE_CLIPPED, and the order is still sent), and the order goes to the gate with a client_order_id derived from
the signal id, so a replayed or restarted copier cannot double-submit (the gate returns the original decision).

Everything the filters decide depends only on the feed, the config and the rules, never on the wall clock. The
follower's "replay clock" for a signal is max(previous clock, leader_ts + delay_ms): a signal whose leader_ts is behind
the clock (out of order, or delayed) ages; nothing else does."""

import hashlib
from collections import Counter

from copier.config import FollowerConfig
from copier.feed import Incoming
from core.ledger import Ledger
from leaders.signal import SignalError, validate
from marketdata.calendar import CalendarRangeError, ny_date, session_ms

CODES = ("INVALID_SIGNAL", "UNTRUSTED_LEADER", "DUPLICATE_SIGNAL", "UNKNOWN_SYMBOL", "MARKET_CLOSED", "STALE_SIGNAL",
         "FEED_GAP", "LEADER_DRAWDOWN_STOP", "SELL_WITHOUT_POSITION", "SIZE_ROUNDS_TO_ZERO")
CLIPPED = "SIZE_CLIPPED"      # logged, not a skip: a smaller order was sent


def order_id_for(signal_id: str) -> str:
    """Deterministic, gate-legal (^[A-Za-z0-9_-]{1,64}$) and independent of the process or the run."""
    return "cp-" + hashlib.sha256(signal_id.encode()).hexdigest()[:24]


def scale_qty(qty: int, num: int, den: int) -> int:
    """Integer scaling, rounded DOWN (toward zero): the follower never takes more than its ratio of the leader's size."""
    return qty * num // den


def tradable(ts_ms: int) -> bool:
    """True from the open to the close of a New York trading day, both inclusive. A signal is stamped with the END of
    its bar, so the last bar of the day ends exactly at the close; treating that instant as tradable lets a leader's
    closing exit be copied. Outside the calendar's years (or on a weekend/holiday) the market counts as closed."""
    try:
        s = session_ms(ny_date(ts_ms))
    except CalendarRangeError:
        return False
    return s is not None and s[0] <= ts_ms <= s[1]


class _Leader:
    """The leader's own paper P&L from its signals (fills at ref_price, no fees): realized, and its peak."""

    def __init__(self):
        self.ledger, self.peak = Ledger(), 0

    def drawdown(self) -> int:
        return self.peak - self.ledger.realized_gross

    def apply(self, sig):
        self.ledger.fill(sig.symbol, sig.side, sig.qty, sig.ref_price, 0)
        self.peak = max(self.peak, self.ledger.realized_gross)


class Copier:
    def __init__(self, cfg: FollowerConfig, symbols, submit, skiplog):
        """submit(order_dict) -> gate reply dict. skiplog.append(signal_id, code, text)."""
        self.cfg, self.symbols, self.submit, self.skiplog = cfg, set(symbols), submit, skiplog
        self.seen_ids: set[str] = set()
        self.last_seq: dict[str, int] = {}
        self.leaders: dict[str, _Leader] = {}
        self.pos: dict[str, int] = {}          # the follower's own position, from the gate's ACCEPT replies
        self.clock = 0
        self.stats = {"seen": 0, "sent": 0, "accepted": 0, "resent": 0}
        self.skipped, self.rejected = Counter(), Counter()
        self.clipped = 0

    def _skip(self, signal_id, code, text):
        self.skipped[code] += 1
        self.skiplog.append(signal_id, code, text)
        return {"copied": False, "reason_code": code}

    def process(self, inc: Incoming) -> dict:
        self.stats["seen"] += 1
        cfg = self.cfg
        # 1. INVALID_SIGNAL
        try:
            sig = validate(inc.raw)
        except SignalError as e:
            return self._skip(f"{inc.signer}:{inc.ref}", "INVALID_SIGNAL", f"The signal is malformed and was ignored: {e}.")
        sid = f"{inc.signer}:{sig.leader_seq}"
        # 2. UNTRUSTED_LEADER
        if inc.signer not in cfg.allowlist:
            return self._skip(sid, "UNTRUSTED_LEADER", f"Leader '{inc.signer}' is not on this follower's allowlist.")
        # 3. DUPLICATE_SIGNAL
        if sid in self.seen_ids:
            return self._skip(sid, "DUPLICATE_SIGNAL", f"Signal {sid} was already received; it is never executed twice.")
        self.seen_ids.add(sid)
        leader = self.leaders.setdefault(inc.signer, _Leader())
        prev_seq, self.last_seq[inc.signer] = self.last_seq.get(inc.signer), sig.leader_seq
        drawdown = leader.drawdown()            # as it stood before this signal
        leader.apply(sig)
        self.clock = max(self.clock, sig.leader_ts + cfg.delay_ms)
        # 4. UNKNOWN_SYMBOL
        if sig.symbol not in self.symbols:
            return self._skip(sid, "UNKNOWN_SYMBOL", f"{sig.symbol} is not traded by this follower (no price anchor or limits for it).")
        # 5. MARKET_CLOSED
        if not tradable(self.clock):
            return self._skip(sid, "MARKET_CLOSED", "The US equity market (America/New_York calendar) is closed at the follower's clock.")
        # 6. STALE_SIGNAL
        age = self.clock - sig.leader_ts
        if age > cfg.max_age_ms:
            return self._skip(sid, "STALE_SIGNAL", f"The signal is {age} ms old on the follower's clock; the maximum is {cfg.max_age_ms} ms.")
        # 7. FEED_GAP
        if prev_seq is not None and sig.leader_seq != prev_seq + 1:
            return self._skip(sid, "FEED_GAP", f"Leader '{inc.signer}' signals {prev_seq + 1}..{sig.leader_seq - 1} never arrived; "
                                               "this one may depend on them.")
        # 8. LEADER_DRAWDOWN_STOP (only for orders that would grow the follower's position, so a position can always be closed)
        cur = self.pos.get(sig.symbol, 0)
        # 9. SELL_WITHOUT_POSITION (close-only: nothing to sell means nothing to copy)
        if sig.side == "SELL" and not cfg.allow_short and cur <= 0:
            return self._skip(sid, "SELL_WITHOUT_POSITION",
                              f"You don't hold {sig.symbol}, so there is nothing to sell (this follower never goes short).")
        qty = scale_qty(sig.qty, cfg.scale_num, cfg.scale_den)
        if qty > 0:
            delta = qty if sig.side == "BUY" else -qty
            if drawdown >= cfg.leader_drawdown_stop and abs(cur + delta) > abs(cur):
                return self._skip(sid, "LEADER_DRAWDOWN_STOP",
                                  f"The leader is down {drawdown} cents from its peak; this follower stops copying new risk at {cfg.leader_drawdown_stop}.")
        # scaling
        if qty == 0:
            return self._skip(sid, "SIZE_ROUNDS_TO_ZERO",
                              f"Leader qty {sig.qty} scaled by {cfg.scale_num}/{cfg.scale_den} rounds down to 0 shares.")
        if sig.side == "SELL" and not cfg.allow_short and qty > cur:
            self.skiplog.append(sid, CLIPPED, f"Leader SELL {sig.qty} scaled to {qty}, clipped to the {cur} {sig.symbol} "
                                              f"share(s) you hold; a SELL of {cur} was sent.")
            self.clipped += 1
            qty = cur
        reply = self.submit({"client_order_id": order_id_for(sid), "symbol": sig.symbol, "side": sig.side,
                             "qty": qty, "price": sig.ref_price})
        if "error" in reply:
            raise RuntimeError(f"the gate refused a well-formed order as an error: {reply['error']}")
        self.stats["sent"] += 1
        self.stats["resent"] += bool(reply.get("duplicate"))
        if reply["decision"] == "ACCEPT":
            self.stats["accepted"] += 1
            self.pos[sig.symbol] = cur + (qty if sig.side == "BUY" else -qty)
        else:
            self.rejected[reply["reason_code"]] += 1
        return {"copied": True, "decision": reply["decision"], "reason_code": reply["reason_code"]}

    def run(self, incoming) -> dict:
        for inc in incoming:
            self.process(inc)
        return self.summary()

    def summary(self) -> dict:
        return {**self.stats, "clipped": self.clipped, "skipped": dict(sorted(self.skipped.items())), "rejected": dict(sorted(self.rejected.items()))}
