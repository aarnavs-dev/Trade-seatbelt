"""Deterministic order stream. Same (seed, profile, anchors, limit) -> same orders and gaps, every time.

The stream NEVER depends on the gate's answers (those depend on wall-clock rate/cooldown timing). A "loss" for the
tilt profile is judged from the trader's own intended fills (assumed filled at the order price), so streams replay."""

import random
from typing import NamedTuple

from sim.model import PriceModel
from sim.profiles import PROFILES

BURST_GAP = 0.05          # relative gap inside a burst (1.0 = the normal gap at the chosen speed)
MAX_TILT = 3


class Item(NamedTuple):
    order: dict      # exactly the fields the gate accepts: client_order_id, symbol, side, qty, price (cents)
    gap: float       # relative wait before this order; actual seconds = gap / speed
    tags: tuple      # what the profile did: 'oversize', 'off_market', 'fat_finger', 'burst', 'revenge'


class OrderStream:
    def __init__(self, seed: int, profile: str, anchors: dict, position_limit: int = 100):
        self.seed, self.profile, self.limit = seed, PROFILES[profile], position_limit
        self.rng = random.Random(f"{seed}:trader")
        self.model = PriceModel(seed, anchors)
        self.symbols = sorted(anchors)
        self.pos = {s: 0 for s in self.symbols}      # the trader's own belief (assumes every order fills)
        self.avg = {s: 0.0 for s in self.symbols}
        self.n = 0
        self.burst_left = 0
        self.tilt = 0
        self.last_loss = None                        # (symbol, side of the losing position)

    def _book(self, sym, side, qty, price) -> float:
        """Average-cost bookkeeping for the trader's own mind-state. Returns realized P&L of this fill (cents)."""
        signed, pos, realized = (qty if side == "BUY" else -qty), self.pos[sym], 0.0
        if pos == 0 or (pos > 0) == (signed > 0):
            self.avg[sym] = (self.avg[sym] * abs(pos) + price * qty) / (abs(pos) + qty)
        else:
            closed = min(abs(pos), qty)
            realized = (price - self.avg[sym]) * closed * (1 if pos > 0 else -1)
            if abs(signed) > abs(pos):
                self.avg[sym] = price
        self.pos[sym] = pos + signed
        if self.pos[sym] == 0:
            self.avg[sym] = 0.0
        return realized

    def next(self) -> Item:
        p, rng, tags = self.profile, self.rng, []
        self.n += 1
        self.model.step()

        gap = rng.uniform(0.5, 1.5)
        if self.burst_left > 0:
            self.burst_left, gap = self.burst_left - 1, BURST_GAP
            tags.append("burst")
        elif rng.random() < p.burst_p:
            self.burst_left, gap = rng.randint(2, 7), BURST_GAP
            tags.append("burst")
        if p.tilt and self.tilt:
            gap /= 1 + self.tilt

        revenge = p.tilt and self.tilt > 0 and self.last_loss is not None and rng.random() < 0.8
        sym = self.last_loss[0] if revenge else rng.choice(self.symbols)
        pos = self.pos[sym]

        if revenge:
            side = self.last_loss[1]
            tags.append("revenge")
        elif pos and (abs(pos) >= self.limit * 0.6 or rng.random() < 0.45):
            side = "SELL" if pos > 0 else "BUY"      # reduce
        else:
            side = rng.choice(("BUY", "SELL"))

        qty = rng.randint(*p.qty_range)
        if p.tilt and self.tilt:
            qty = round(qty * (1 + 0.6 * self.tilt))
        if rng.random() < p.oversize_p:
            qty = rng.randint(self.limit, int(self.limit * 2.5))
            tags.append("oversize")
        elif p.respects_limit:
            signed = qty if side == "BUY" else -qty
            if abs(pos + signed) > abs(pos):          # would grow the position: clamp to headroom
                qty = max(1, min(qty, self.limit - abs(pos)))
                if abs(pos) >= self.limit:
                    side, qty = ("SELL" if pos > 0 else "BUY"), min(qty, abs(pos))

        mkt = self.model.cents(sym)
        price = round(mkt * (1 + rng.uniform(-p.jitter, p.jitter)))
        if rng.random() < p.offmarket_p:
            price = round(mkt * (1 + rng.choice((-1, 1)) * rng.uniform(0.06, 0.12)))
            tags.append("off_market")
        if rng.random() < p.fatfinger_p:
            price = price * 10 if rng.random() < 0.5 else max(1, price // 10)
            tags.append("fat_finger")
        price = max(1, price)

        # the trader's own accounting skips deliberate anomalies (it expects those to be refused)
        if not ({"oversize", "off_market", "fat_finger"} & set(tags)):
            realized = self._book(sym, side, qty, price)
            if realized < 0:
                self.tilt, self.last_loss = min(MAX_TILT, self.tilt + 1), (sym, "BUY" if side == "SELL" else "SELL")
            elif realized > 0:
                self.tilt = max(0, self.tilt - 1)

        oid = f"o-{self.seed:016x}-{self.n:06d}"
        return Item({"client_order_id": oid, "symbol": sym, "side": side, "qty": qty, "price": price},
                    round(gap, 6), tuple(tags))
