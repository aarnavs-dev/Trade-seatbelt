"""Generate fixtures/orders.jsonl and fixtures/expected.json.

SCENARIO is hand-written; every outcome below was worked out by hand (prices in cents, fee =
max(qty*1, 50)). The seed only decides 12 'noise' orders that are rejected whatever the state is
(6 outside the price band, 6 far above the position limit) and where they are inserted, so the
expected totals do not depend on the seed.

 id    order                       result                          gross   fee
 A001  BUY  AAPL 10 @19000         ACCEPT (lot 10@19000)                    50
 A002  BUY  AAPL 20 @19100         ACCEPT (lot 20@19100)                    50
 A003  SELL AAPL 15 @19200         closes 10@19000 (+2000) 5@19100 (+500)  +2500  50
 A004  SELL AAPL 15 @19050         closes 15@19100 (-750)   loss 1          -750  50
 A004  (sent again)                duplicate: original decision, not counted
 M001  BUY  MSFT 100 @42000        ACCEPT (position 100 = limit)                  100
 M002  BUY  MSFT 1 @42000          REJECT MAX_POSITION (101 > 100)
 A005  BUY  AAPL 10 @21000         REJECT PRICE_BAND (10.5% > 5%)
 A006  BUY  AAPL 10 @19000         ACCEPT                                          50
 A007  SELL AAPL 10 @18900         -1000, loss 2                                  -1000  50
 A008  BUY  AAPL 10 @19000         ACCEPT                                          50
 A009  SELL AAPL 10 @18900         -1000, loss 3 -> cooldown 300s                 -1000  50
 A010  BUY  AAPL 10 @19000         REJECT LOSS_STREAK_COOLDOWN
 M003  SELL MSFT 100 @42100        closing is allowed in cooldown, +10000       +10000  100
Totals: AAPL gross -250 fees 400 net -650; MSFT gross 10000 fees 200 net 9800;
        all gross 9750, fees 600, net 9150, cash 9150, every position flat.
"""

import argparse
import json
import os
import random

SCENARIO = [
    ("A001", "BUY", "AAPL", 10, 19000), ("A002", "BUY", "AAPL", 20, 19100),
    ("A003", "SELL", "AAPL", 15, 19200), ("A004", "SELL", "AAPL", 15, 19050),
    ("A004", "SELL", "AAPL", 15, 19050),
    ("M001", "BUY", "MSFT", 100, 42000), ("M002", "BUY", "MSFT", 1, 42000),
    ("A005", "BUY", "AAPL", 10, 21000),
    ("A006", "BUY", "AAPL", 10, 19000), ("A007", "SELL", "AAPL", 10, 18900),
    ("A008", "BUY", "AAPL", 10, 19000), ("A009", "SELL", "AAPL", 10, 18900),
    ("A010", "BUY", "AAPL", 10, 19000), ("M003", "SELL", "MSFT", 100, 42100),
]
REF = {"AAPL": 19000, "MSFT": 42000}
EXPECTED = {
    "events": 25, "accepted": 10, "rejected": 15, "duplicates_sent": 1,
    "rejects_by_reason": {"LOSS_STREAK_COOLDOWN": 1, "MAX_POSITION": 7, "PRICE_BAND": 7},
    "realized_gross": 9750, "fees": 600, "realized_net": 9150, "cash": 9150, "positions": {},
    "by_symbol": {"AAPL": {"gross": -250, "fees": 400, "net": -650},
                  "MSFT": {"gross": 10000, "fees": 200, "net": 9800}},
}


def build(seed: int) -> list[dict]:
    rng = random.Random(seed)
    orders = [dict(client_order_id=i, side=s, symbol=y, qty=q, price=p) for i, s, y, q, p in SCENARIO]
    for n in range(12):
        sym, side = rng.choice(sorted(REF)), rng.choice(["BUY", "SELL"])
        if n < 6:  # outside the 5% band whatever the state
            price, qty = rng.choice([REF[sym] * 12 // 10, REF[sym] * 8 // 10]), rng.randint(1, 5)
        else:      # in band, but far beyond the 100 share limit
            price, qty = REF[sym] + rng.randint(-100, 100), rng.randint(1000, 2000)
        orders.insert(rng.randrange(len(orders) + 1),
                      dict(client_order_id=f"N{n + 1:03d}", side=side, symbol=sym, qty=qty, price=price))
    return orders


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="fixtures")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "orders.jsonl"), "w") as f:
        for o in build(a.seed):
            f.write(json.dumps(o, sort_keys=True, separators=(",", ":")) + "\n")
    with open(os.path.join(a.out, "expected.json"), "w") as f:
        json.dump(EXPECTED, f, indent=2, sort_keys=True)
        f.write("\n")
    print(f"wrote {a.out}/orders.jsonl (seed {a.seed}) and expected.json")
