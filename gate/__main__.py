import argparse
import asyncio
import cProfile
import os
import signal

from core.logio import FSYNC_POLICIES, ChainError
from core.rules import load_rules
from gate.engine import Gate
from gate.server import start_server


async def main(args) -> None:
    gate = Gate(load_rules(args.rules), args.log, args.fsync, args.batch_max, full_fsync=args.full_fsync)
    try:
        await gate.start()
    except ChainError as e:
        raise SystemExit(f"refusing to start: {e}")
    server = await start_server(gate, args.host, args.port)
    print(f"gate listening on {args.host}:{args.port} log={args.log} fsync={args.fsync}{'+F_FULLFSYNC' if args.full_fsync else ''} "
          f"batch_max={gate.batch_max} replayed_events={gate.book.last_seq} "
          f"torn_tail_bytes_removed={gate.recovery_note['bytes_removed'] if gate.recovery_note else 0}", flush=True)
    stop = asyncio.Event()
    profile_path = os.environ.get("GATE_PROFILE")  # benchmark hook, off by default
    if profile_path:
        prof, loop = cProfile.Profile(), asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGUSR1, prof.enable)  # start of measured window
        loop.add_signal_handler(signal.SIGUSR2, lambda: (prof.disable(), prof.dump_stats(profile_path)))
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    await stop.wait()
    server.close()
    await gate.stop()
    print(f"gate stopped at seq {gate.book.last_seq} head={gate.book.last_hash} "
          f"state_hash={gate.book.state_hash()}", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="gate")
    p.add_argument("--rules", default="rules.yaml")
    p.add_argument("--log", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9101)
    p.add_argument("--fsync", choices=FSYNC_POLICIES, default="batched")
    p.add_argument("--full-fsync", action="store_true", help="macOS only: F_FULLFSYNC instead of os.fsync")
    p.add_argument("--batch-max", type=int, default=256)
    args = p.parse_args()
    try:
        import uvloop
        uvloop.run(main(args))
    except ImportError:
        asyncio.run(main(args))
