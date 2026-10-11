import asyncio
import itertools

import orjson

from core.rules import load_rules
from gate.engine import Gate

RULES = load_rules("rules.yaml")


def counter_clock(step=1_000_000):
    c = itertools.count(1_000_000_000_000, step)
    return lambda: next(c)


def line(oid, side, symbol, qty, price) -> bytes:
    return orjson.dumps(dict(client_order_id=oid, side=side, symbol=symbol, qty=qty, price=price)) + b"\n"


async def run_gate(path, lines, fsync="batched", clock=None, rules=RULES, sequential=True):
    """Submit lines, return parsed responses. Gate is started and stopped here."""
    gate = Gate(rules, path, fsync, clock=clock or counter_clock())
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
