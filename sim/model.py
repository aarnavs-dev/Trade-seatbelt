"""Price model: independent geometric Brownian motion per symbol, starting at the snapshot anchor.

    P[t+1] = P[t] * exp((MU - SIGMA^2/2) * DT + SIGMA * sqrt(DT) * Z),   Z ~ N(0, 1)

MU = 0 (no drift assumed), SIGMA = 0.30 per year, and one step (one order) stands for one simulated trading
minute: DT = 1 / (252 * 390). This is a BASIC model, not a prediction: no mean reversion, no fat tails, no
correlation between symbols, no order book or market impact. The reference prices in the rules are static, so a
long run can wander outside the price band; that shows up as PRICE_BAND rejects and is a known limitation.
Randomness comes only from random.Random seeded with the run seed."""

import math
import random

MU = 0.0
SIGMA = 0.30
DT = 1.0 / (252 * 390)


class PriceModel:
    def __init__(self, seed: int, anchors: dict):
        self.rng = random.Random(f"{seed}:price")  # str seeding is hashed with sha512: stable across runs/versions
        self.symbols = sorted(anchors)
        self.px = {s: anchors[s] / 100 for s in self.symbols}
        self._drift = (MU - 0.5 * SIGMA ** 2) * DT
        self._vol = SIGMA * math.sqrt(DT)

    def step(self) -> None:
        for s in self.symbols:  # fixed order so the draw sequence never depends on dict history
            self.px[s] *= math.exp(self._drift + self._vol * self.rng.gauss(0.0, 1.0))

    def cents(self, symbol: str) -> int:
        return max(1, round(self.px[symbol] * 100))
