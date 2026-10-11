"""Send orders from a fixture file to the gate and print each decision."""

import argparse
import asyncio
import time
from collections import Counter, deque

import orjson


async def connect(host, port, wait):
    deadline = time.monotonic() + wait
    while True:
        try:
            return await asyncio.open_connection(host, port)
        except OSError:
            if time.monotonic() > deadline:
                raise SystemExit(f"could not connect to {host}:{port}")
            await asyncio.sleep(0.1)


async def run(args) -> None:
    lines = [l.rstrip(b"\n") + b"\n" for l in open(args.fixture, "rb") if l.strip()]
    reader, writer = await connect(args.host, args.port, args.wait)
    sent, window = deque(), asyncio.Semaphore(args.window)

    async def sender():
        for line in lines:
            await window.acquire()
            sent.append(line)
            writer.write(line)
            await writer.drain()

    t0 = time.perf_counter()
    send_task = asyncio.create_task(sender())
    tally = Counter()
    for n in range(1, len(lines) + 1):
        resp = orjson.loads(await reader.readline())
        window.release()
        o = orjson.loads(sent.popleft())
        if "error" in resp:
            tally["ERROR"] += 1
            print(f"{n:>3} {o.get('client_order_id', '?'):<6} error: {resp['error']}")
            continue
        tally["DUPLICATE" if resp["duplicate"] else resp["decision"]] += 1
        tag = " (duplicate, original decision)" if resp["duplicate"] else ""
        if not args.quiet:
            print(f"{n:>3} {o['client_order_id']:<6} {o['side']:<4} {o['qty']:>5} {o['symbol']} "
                  f"@ {o['price']:>6} -> seq {resp['seq']:>2} {resp['decision']:<6} "
                  f"{resp['reason_code']}{tag}")
    await send_task
    elapsed = time.perf_counter() - t0
    writer.close()
    print(f"sent {len(lines)} orders in {elapsed * 1000:.1f} ms: " +
          ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="client")
    p.add_argument("--fixture", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9101)
    p.add_argument("--window", type=int, default=64, help="max requests in flight")
    p.add_argument("--wait", type=float, default=5.0, help="seconds to retry connecting")
    p.add_argument("--quiet", action="store_true")
    asyncio.run(run(p.parse_args()))
