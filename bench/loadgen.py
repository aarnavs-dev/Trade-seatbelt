"""Separate load-generator process. Not part of the gate.

closed:    one order in flight per connection; latency = send -> decision reply, measured here.
pipelined: up to --window in flight per connection; only throughput is meaningful.
Protocol with the harness: connect, run warm-up, print READY, wait for GO on stdin, run the
measured phase, write a result JSON. --epilogue sends the trailing cooldown orders instead.
"""

import argparse
import asyncio
import json
import sys
import time
from collections import deque

import orjson

try:
    import uvloop
except ImportError:  # pragma: no cover
    uvloop = None


class Stats:
    def __init__(self):
        self.errors = self.mismatches = 0


async def drive(reader, writer, items, mode, window, expected, lat, st):
    def check(raw, oid):
        r = orjson.loads(raw)
        if "error" in r:
            st.errors += 1
        elif r["duplicate"] or r["reason_code"] != expected[oid]:
            st.mismatches += 1

    if mode == "closed":
        now = time.perf_counter_ns
        for oid, line in items:
            t = now()
            writer.write(line)
            raw = await reader.readline()
            if lat is not None:
                lat.append(now() - t)
            check(raw, oid)
        return
    sem, ids = asyncio.Semaphore(window), deque()

    async def sender():
        for k, (oid, line) in enumerate(items):
            await sem.acquire()
            ids.append(oid)
            writer.write(line)
            if k % 64 == 63:
                await writer.drain()
        await writer.drain()

    task = asyncio.create_task(sender())
    for _ in items:
        raw = await reader.readline()
        sem.release()
        check(raw, ids.popleft())
    await task


async def main(a) -> None:
    expected = json.load(open(a.expected))["decisions"]
    lines = open(a.workload, "rb").read().splitlines(keepends=True)
    main_n = len(lines) - 306
    if a.epilogue:
        streams = {0: [(orjson.loads(l)["client_order_id"], l) for l in lines[main_n:]]}
        conns, warm = [0], {0: []}
    else:
        mine = [int(c) for c in a.mine.split(",")]
        streams = {c: [] for c in mine}
        for i, l in enumerate(lines[:main_n]):
            c = int(orjson.loads(l)["symbol"][3:5]) % a.conns
            if c in streams:
                streams[c].append((i, orjson.loads(l)["client_order_id"], l))
        conns = mine
        warm = {c: [(o, l) for i, o, l in streams[c] if i < a.warmup] for c in conns}
        streams = {c: [(o, l) for i, o, l in streams[c] if i >= a.warmup] for c in conns}
    st, socks = Stats(), {}
    for c in conns:
        socks[c] = await asyncio.open_connection("127.0.0.1", a.port, limit=1 << 16)
    go = lambda items, lat: asyncio.gather(*(
        drive(*socks[c], items[c], a.mode, a.window, expected, lat[c], st) for c in conns))
    if not a.epilogue:
        await go(warm, {c: None for c in conns})  # warm-up: not timed, not recorded
        print("READY", flush=True)
        await asyncio.get_running_loop().run_in_executor(None, sys.stdin.readline)
    lat = {c: ([] if a.mode == "closed" and not a.epilogue else None) for c in conns}
    t0 = time.monotonic_ns()
    await go(streams, lat)
    t1 = time.monotonic_ns()
    for _, w in socks.values():
        w.close()
    json.dump({"t0": t0, "t1": t1, "measured": sum(len(s) for s in streams.values()),
               "lat_ns": [x for c in conns for x in lat[c]] if lat[conns[0]] is not None else None,
               "errors": st.errors, "mismatches": st.mismatches}, open(a.result, "w"))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--workload", required=True)
    p.add_argument("--expected", required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--mode", choices=["closed", "pipelined"], default="closed")
    p.add_argument("--conns", type=int, default=1, help="total connections across all processes")
    p.add_argument("--mine", default="0", help="comma list of connection indexes this process runs")
    p.add_argument("--warmup", type=int, default=5000)
    p.add_argument("--window", type=int, default=256)
    p.add_argument("--epilogue", action="store_true")
    a = p.parse_args()
    (uvloop.run if uvloop else asyncio.run)(main(a))
