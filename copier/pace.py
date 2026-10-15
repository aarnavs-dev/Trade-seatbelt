"""Pace: how long the follower waits between visible signals. It changes TIMING ONLY: nothing here is read by a rule, a
filter or the gate, so decisions are identical at every pace (tested with an injected clock). Delays are seeded, so the
same (pace, seed) always waits the same amounts. The wait exists so the rows on /follow arrive slowly enough to read."""

import random

RANGES = {"relaxed": (4.0, 8.0), "normal": (2.0, 4.0), "fast": (0.4, 1.0)}     # seconds between signals
NAMES = tuple(RANGES) + ("off",)


class Pace:
    def __init__(self, name: str, seed: int):
        if name not in RANGES:
            raise ValueError(f"pace must be one of {', '.join(RANGES)}")
        self.name, self.seed = name, seed

    def delay(self, i: int) -> float:
        lo, hi = RANGES[self.name]
        return round(random.Random(f"{self.seed}:{self.name}:{i}").uniform(lo, hi), 3)

    def to_dict(self) -> dict:
        return {"name": self.name, "seed": self.seed}


def from_manifest(d) -> "Pace | None":
    return Pace(d["name"], d["seed"]) if d and d.get("name") in RANGES else None
