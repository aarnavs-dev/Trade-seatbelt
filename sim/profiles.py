"""Trader profiles. Numbers are documented knobs of a toy behaviour model, not calibrated to real traders."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    qty_range: tuple          # normal order size, shares
    oversize_p: float         # chance of an order well past the position limit
    offmarket_p: float        # chance of a price 6-12% away from the market
    fatfinger_p: float        # chance of a decimal/digit slip (price x10 or /10)
    burst_p: float            # chance an order starts a burst of 3-8 rapid orders
    respects_limit: bool      # sizes orders to the remaining headroom
    tilt: bool = False        # revenge trading after a loss
    jitter: float = 0.0005    # normal price noise around the market (fraction)


PROFILES = {
    "disciplined": Profile("disciplined", "Mostly inside its own rules; small sizes, prices near the market.",
                           (5, 30), 0.0, 0.0, 0.002, 0.01, True),
    "impulsive": Profile("impulsive", "Sometimes oversizes, sometimes uses prices far from the market.",
                         (10, 80), 0.12, 0.10, 0.01, 0.04, False, jitter=0.002),
    "tilt": Profile("tilt", "After a loss it re-enters faster and larger (revenge trading).",
                    (5, 40), 0.02, 0.01, 0.005, 0.03, False, tilt=True, jitter=0.001),
}
