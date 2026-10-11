"""FIFO ledger. Integers only. A fill is a full fill at the order price (simulation)."""

from collections import deque


class Ledger:
    def __init__(self):
        self.lots: dict[str, deque] = {}  # symbol -> deque of [signed_qty, price], all same sign
        self.pos: dict[str, int] = {}
        self.cash = 0
        self.realized_gross = 0
        self.fees = 0
        self.by_symbol: dict[str, list[int]] = {}  # symbol -> [gross, fees]

    def position(self, symbol: str) -> int:
        return self.pos.get(symbol, 0)

    def fill(self, symbol: str, side: str, qty: int, price: int, fee: int) -> tuple[int, int]:
        """Apply a fill. Returns (realized gross P&L of this fill, qty closed)."""
        signed = qty if side == "BUY" else -qty
        lots = self.lots.setdefault(symbol, deque())
        remaining, realized, closed = signed, 0, 0
        while remaining and lots and (lots[0][0] > 0) != (remaining > 0):
            lot = lots[0]
            m = min(abs(lot[0]), abs(remaining))
            realized += (price - lot[1]) * m if lot[0] > 0 else (lot[1] - price) * m
            closed += m
            step = m if remaining > 0 else -m
            lot[0] += step
            remaining -= step
            if lot[0] == 0:
                lots.popleft()
        if remaining:
            lots.append([remaining, price])
        self.pos[symbol] = self.pos.get(symbol, 0) + signed
        self.cash += -signed * price - fee
        self.realized_gross += realized
        self.fees += fee
        row = self.by_symbol.setdefault(symbol, [0, 0])
        row[0] += realized
        row[1] += fee
        return realized, closed

    def identity_ok(self) -> bool:
        """cash == realized gross - fees - cost of open lots. Holds after every fill."""
        open_cost = sum(q * p for lots in self.lots.values() for q, p in lots)
        return self.cash == self.realized_gross - self.fees - open_cost

    def snapshot(self) -> dict:
        return {
            "cash": self.cash,
            "realized_gross": self.realized_gross,
            "fees": self.fees,
            "positions": {s: q for s, q in sorted(self.pos.items()) if q},
            "lots": {s: [list(l) for l in lots] for s, lots in sorted(self.lots.items()) if lots},
        }
