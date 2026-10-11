"""Seeded benchmark workload plus its expected decisions.

Decisions must not depend on how requests from different connections interleave, so the expected
totals are exact for any connection count. That is arranged by:
  * one connection owns whole symbols (symbol index % connections), so position-limit decisions
    only depend on that connection's own order;
  * daily-loss and rate limit are set so high that they never fire (bench rules);
  * every closing trade in the main phase is a gain, so the loss streak never moves;
  * the cooldown is exercised by a sequential epilogue on its own symbol, after the main phase.
The generator runs the real core path (Book.verdict/make_event/apply), so the reference model is
core's check(), and its output doubles as a valid hash-chained log (used for recovery timing).
"""

import argparse
import json
import os
import random
from collections import Counter

import orjson
import yaml

from core.book import Book
from core.events import Order
from core.logio import event_line
from core.rules import Rules

N_SYMS, EPI, EPILOGUE = 32, "EPI", 306
BASE_TS = 1_700_000_000 * 10**9
DATA = os.path.join(os.path.dirname(__file__), "data")


def sym(i: int) -> str:
    return f"SYM{i:02d}"


def ref(i: int) -> int:
    return 10_000 + 1_000 * i


def rules_dict() -> dict:
    syms = {sym(i): {"reference_price": ref(i)} for i in range(N_SYMS)}
    syms[EPI] = {"reference_price": 20_000}
    return {
        "version": 1, "symbols": syms, "max_position_per_symbol": 100,
        "max_daily_loss": 10**12, "price_band_pct": 5, "max_orders_per_minute": 10**9,
        "loss_streak_cooldown": {"losses": 3, "cooldown_seconds": 86_400},
        "kill_switch": False, "fees": {"per_share_minor": 1, "min_per_order_minor": 50},
    }


def write_rules(path: str) -> None:
    with open(path, "w") as f:
        yaml.safe_dump(rules_dict(), f, sort_keys=True)


def generate(n: int, seed: int = 7, log_path: str | None = None, keep: bool = True):
    """Returns (order_lines, expected). The last EPILOGUE orders are the cooldown epilogue."""
    assert n > EPILOGUE
    rng, book = random.Random(seed), Book(Rules.model_validate(rules_dict()))
    out = open(log_path, "wb") if log_path else None
    lines, decisions, by_reason, accepted, count = [], {}, Counter(), 0, 0

    def emit(oid, side, symbol, qty, price):
        nonlocal accepted, count
        o = dict(client_order_id=oid, side=side, symbol=symbol, qty=qty, price=price)
        order, ts = Order(**o), BASE_TS + count * 1000
        count += 1
        ev = book.make_event(order, ts, book.verdict(order, ts))
        book.apply(ev)
        accepted += ev.decision == "ACCEPT"
        by_reason[ev.reason_code] += 1
        if keep:
            lines.append(orjson.dumps(o) + b"\n")
            decisions[oid] = ev.reason_code
        if out:
            out.write(event_line(ev))

    for i in range(n - EPILOGUE):
        s = rng.randrange(N_SYMS)
        r, name, base, pos = rng.random(), sym(s), ref(s), book.ledger.position(sym(s))
        j = rng.randint(0, 20)
        if r < 0.08:    # outside the price band
            emit(f"B{i:07d}", rng.choice(["BUY", "SELL"]), name, rng.randint(1, 30),
                 base * 11 // 10 if rng.random() < 0.5 else base * 9 // 10)
        else:
            if r < 0.16:  # far larger than the position limit
                side, qty = rng.choice(["BUY", "SELL"]), rng.randint(150, 300)
            else:         # normal flow, biased back toward flat
                side = "SELL" if rng.random() < min(1, max(0, 0.5 + pos / 200)) else "BUY"
                qty = rng.randint(1, 30)
            # buys below the reference, sells above it: every close is a gain
            emit(f"B{i:07d}", side, name, qty, base - j if side == "BUY" else base + 50 + j)
    assert book.cooldown_until == 0 and book.loss_streak == 0, "main phase must not lose"
    for k in range(3):  # three losing round trips -> cooldown
        emit(f"E{k}B", "BUY", EPI, 10, 20_000)
        emit(f"E{k}S", "SELL", EPI, 10, 19_800)
    for k in range(300):
        emit(f"E{k:03d}C", "BUY", EPI, 1, 20_000)
    if out:
        out.close()
    return lines, {"n": n, "main": n - EPILOGUE, "accepted": accepted, "rejected": n - accepted,
                   "by_reason": dict(sorted(by_reason.items())), "decisions": decisions}


def load(n: int, seed: int = 7):
    """Cached workload: (path to orders.jsonl, expected dict)."""
    os.makedirs(DATA, exist_ok=True)
    base = os.path.join(DATA, f"w{n}_s{seed}")
    if not os.path.exists(base + ".jsonl"):
        lines, expected = generate(n, seed)
        with open(base + ".jsonl", "wb") as f:
            f.write(b"".join(lines))
        with open(base + ".expected.json", "w") as f:
            json.dump(expected, f)
    with open(base + ".expected.json") as f:
        return base + ".jsonl", json.load(f)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=100_000)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--emit-log", help="also write the decisions as a valid hash-chained log")
    a = p.parse_args()
    _, e = generate(a.n, a.seed, a.emit_log, keep=not a.emit_log)
    print(f"n={e['n']} accepted={e['accepted']} rejected={e['rejected']} {e['by_reason']}")
