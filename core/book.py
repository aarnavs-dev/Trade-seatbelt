"""All state derived from the log. The gate and the auditor both run events through apply()."""

import hashlib
from collections import deque

from core.canon import GENESIS, canon, event_hash
from core.events import Event, Order
from core.ledger import Ledger
from core.rules import Rules, Verdict, check

WINDOW_NS = 60 * 10**9


class Book:
    def __init__(self, rules: Rules):
        self.rules = rules
        self.ledger = Ledger()
        self.last_seq = 0
        self.last_hash = GENESIS
        self.last_ts = 0
        self.seen: dict[str, Event] = {}
        self.order_times: deque[int] = deque()
        self.loss_streak = 0
        self.cooldown_until = 0

    @property
    def realized_net(self) -> int:
        return self.ledger.realized_gross - self.ledger.fees

    def verdict(self, order: Order, ts: int) -> Verdict:
        """Pure with respect to state: reads, never mutates."""
        cutoff, q, stale = ts - WINDOW_NS, self.order_times, 0
        while stale < len(q) and q[stale] <= cutoff:
            stale += 1
        return check(self.rules, order, ts, self.ledger.position(order.symbol),
                     self.realized_net, len(q) - stale, self.cooldown_until)

    def make_event(self, order: Order, ts: int, v: Verdict) -> Event:
        body = dict(
            seq=self.last_seq + 1, ts=ts, client_order_id=order.client_order_id,
            symbol=order.symbol, side=order.side, qty=order.qty, price=order.price,
            decision="ACCEPT" if v.accept else "REJECT", reason_code=v.code, reason_text=v.text,
        )
        return Event(**body, prev_hash=self.last_hash, hash=event_hash(self.last_hash, body))

    def apply(self, ev: Event) -> None:
        self.last_seq, self.last_hash, self.last_ts = ev.seq, ev.hash, ev.ts
        self.seen[ev.client_order_id] = ev
        if ev.reason_code != "RATE_LIMIT":  # keeps the window bounded by the limit
            self.order_times.append(ev.ts)
        cutoff = ev.ts - WINDOW_NS
        while self.order_times and self.order_times[0] <= cutoff:
            self.order_times.popleft()
        if ev.decision != "ACCEPT":
            return
        fee = self.rules.fees.of(ev.qty)
        gross, closed = self.ledger.fill(ev.symbol, ev.side, ev.qty, ev.price, fee)
        if closed:
            if gross - fee < 0:
                self.loss_streak += 1
                cd = self.rules.loss_streak_cooldown
                if self.loss_streak >= cd.losses:
                    self.cooldown_until = ev.ts + cd.cooldown_seconds * 10**9
                    self.loss_streak = 0
            else:
                self.loss_streak = 0

    def state_hash(self) -> str:
        state = {
            **self.ledger.snapshot(),
            "last_seq": self.last_seq, "last_hash": self.last_hash,
            "seen": {k: v.seq for k, v in sorted(self.seen.items())},
            "loss_streak": self.loss_streak, "cooldown_until": self.cooldown_until,
            "order_times": list(self.order_times),
        }
        return hashlib.sha256(canon(state)).hexdigest()
