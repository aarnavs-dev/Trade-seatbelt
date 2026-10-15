import asyncio
import itertools

import orjson

from core.rules import load_rules
from gate.engine import Gate

RULES = load_rules("rules.yaml")


def counter_clock(step=1_000_000):
    c = itertools.count(1_000_000_000_000, step)
    return lambda: next(c)


def clock_from(values):
    """A clock that returns `values` in order. The gate reads the clock once at start-up for its rules record, so that first
    tick is spent on the record (same value as the first order's, which keeps every order's ts equal to its own value)."""
    it = iter([values[0], *values])
    return lambda: next(it)


def line(oid, side, symbol, qty, price) -> bytes:
    return orjson.dumps(dict(client_order_id=oid, side=side, symbol=symbol, qty=qty, price=price)) + b"\n"


async def run_gate(path, lines, fsync="batched", clock=None, rules=RULES, sequential=True, record_rules=True):
    """Submit lines, return parsed responses. Gate is started and stopped here."""
    gate = Gate(rules, path, fsync, clock=clock or counter_clock(), record_rules=record_rules)
    await gate.start()
    if sequential:
        out = [orjson.loads(await (await gate.submit(l))) for l in lines]
    else:
        futs = [await gate.submit(l) for l in lines]
        out = [orjson.loads(await f) for f in futs]
    await gate.stop()
    return out, gate


def run(coro):
    return asyncio.run(coro)
