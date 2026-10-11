import argparse
import asyncio
import signal

from core.logio import FSYNC_POLICIES, ChainError
from core.rules import load_rules
from gate.engine import Gate
from gate.server import start_server


async def main(args) -> None:
    gate = Gate(load_rules(args.rules), args.log, args.fsync, args.batch_max)
    try:
        await gate.start()
    except ChainError as e:
        raise SystemExit(f"refusing to start: {e}")
    server = await start_server(gate, args.host, args.port)
    print(f"gate listening on {args.host}:{args.port} log={args.log} fsync={args.fsync} "
          f"batch_max={gate.batch_max} replayed_events={gate.book.last_seq}", flush=True)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    server.close()
    await gate.stop()
    print(f"gate stopped at seq {gate.book.last_seq} head={gate.book.last_hash}", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="gate")
    p.add_argument("--rules", default="rules.yaml")
    p.add_argument("--log", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9101)
    p.add_argument("--fsync", choices=FSYNC_POLICIES, default="batched")
    p.add_argument("--batch-max", type=int, default=256)
    args = p.parse_args()
    try:
        import uvloop
        uvloop.run(main(args))
    except ImportError:
        asyncio.run(main(args))
