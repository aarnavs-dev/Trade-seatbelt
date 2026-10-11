"""Run one simulated trader against its own gate.

  python -m sim --provider offline --orders 300            # headless: create a run, trade, print a summary
  python -m sim --run-dir runs/<id>                        # what the API starts (the run was created by create_run)

The simulator starts the gate as a subprocess and is then only a TCP client of it: newline-delimited JSON out,
one reply back per order. It never opens the log. Control comes from runs/<id>/control.json (written by the API),
status goes to runs/<id>/sim.status.json (written here)."""

import argparse
import asyncio
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import orjson

from sim import DEFAULT_MAX_ORDERS, DEFAULT_SPEED, INTERVALS, MAX_SPEED, MIN_SPEED, PROFILES, PROVIDERS
from sim.runfiles import RUN_ID_RE, RunError, create_run, read_json, write_json_atomic
from sim.trader import OrderStream

ROOT = Path(__file__).resolve().parent.parent
GATE_START_TIMEOUT_S = 20.0
REPLY_TIMEOUT_S = 10.0
POLL_S = 0.1
STATUS_EVERY_S = 0.25


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Control:
    """Reads control.json when it changes. Missing file = run at the manifest speed."""

    def __init__(self, path: Path, speed: float):
        self.path, self.state, self.speed, self._mtime = path, "running", speed, None

    def poll(self) -> None:
        try:
            m = self.path.stat().st_mtime_ns
        except OSError:
            return
        if m == self._mtime:
            return
        self._mtime = m
        c = read_json(self.path)
        if isinstance(c, dict):
            if c.get("state") in ("running", "paused", "stopped"):
                self.state = c["state"]
            sp = c.get("speed")
            if isinstance(sp, (int, float)) and not isinstance(sp, bool):
                self.speed = min(MAX_SPEED, max(MIN_SPEED, float(sp)))


class Runner:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.manifest = read_json(run_dir / "manifest.json")
        if not self.manifest:
            raise RunError("run has no manifest")
        snap = read_json(run_dir / "market_snapshot.json")
        self.stream = OrderStream(self.manifest["seed"], self.manifest["profile"], snap["anchors_minor"],
                                  self.manifest["position_limit"])
        self.control = Control(run_dir / "control.json", self.manifest["speed"])
        self.status = {"state": "starting", "pid": None, "port": None, "sent": 0, "accepted": 0, "rejected": 0,
                       "errors": 0, "gate_pid": None, "rejects_by_reason": {}, "speed": self.control.speed, "max_orders": self.manifest["max_orders"],
                       "started_at": _now(), "ended_at": None, "error": None, "updated_at": _now()}
        import os
        self.status["pid"] = os.getpid()
        self.stop_requested = False
        self._last_write = 0.0

    def write_status(self, force=False) -> None:
        t = time.monotonic()
        if force or t - self._last_write >= STATUS_EVERY_S:
            self._last_write = t
            self.status["updated_at"] = _now()
            self.status["speed"] = self.control.speed
            write_json_atomic(self.dir / "sim.status.json", self.status)

    async def start_gate(self):
        port = _free_port()
        out = open(self.dir / "gate.out", "wb")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "gate", "--rules", str(self.dir / "rules.yaml"), "--log", str(self.dir / "gate.log"),
            "--port", str(port), "--fsync", self.manifest["fsync"], cwd=ROOT, stdout=out, stderr=asyncio.subprocess.STDOUT)
        out.close()
        deadline = time.monotonic() + GATE_START_TIMEOUT_S
        while True:
            if proc.returncode is not None:
                raise RuntimeError(f"gate exited early with code {proc.returncode} (see gate.out)")
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                self.status["port"], self.status["gate_pid"] = port, proc.pid
                return proc, reader, writer
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("the gate did not start in time") from None
                await asyncio.sleep(0.05)

    async def nap(self, seconds: float) -> None:
        """Sleep in small slices so pause/stop/speed changes are noticed quickly."""
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0 and not self.stop_requested:
            await asyncio.sleep(min(POLL_S, left))
            self.control.poll()
            if self.control.state != "running":
                return

    async def trade(self, reader, writer) -> None:
        st, pending = self.status, None
        deadline = time.monotonic()
        st["state"] = "running"
        self.write_status(True)
        while st["sent"] < st["max_orders"] and not self.stop_requested:
            self.control.poll()
            if self.control.state == "stopped":
                break
            if self.control.state == "paused":
                st["state"] = "paused"
                self.write_status(True)
                while self.control.state == "paused" and not self.stop_requested:
                    await asyncio.sleep(POLL_S)
                    self.control.poll()
                    self.write_status()
                deadline = time.monotonic()
                st["state"] = "running"
                continue
            if pending is None:
                pending = self.stream.next()
            deadline += pending.gap / self.control.speed
            wait = deadline - time.monotonic()
            if wait > 0:
                await self.nap(wait)
                if self.control.state != "running" or self.stop_requested:
                    deadline = time.monotonic()
                    continue   # keep `pending`; it is sent after resume (or dropped on stop)
            elif wait < -1.0:
                deadline = time.monotonic()   # we fell behind; do not burst to catch up
            writer.write(orjson.dumps(pending.order) + b"\n")
            await writer.drain()
            pending = None
            raw = await asyncio.wait_for(reader.readline(), REPLY_TIMEOUT_S)
            if not raw:
                raise RuntimeError("the gate closed the connection")
            resp = orjson.loads(raw)
            st["sent"] += 1
            if "error" in resp:
                st["errors"] += 1
            elif resp["decision"] == "ACCEPT":
                st["accepted"] += 1
            else:
                st["rejected"] += 1
                r = st["rejects_by_reason"]
                r[resp["reason_code"]] = r.get(resp["reason_code"], 0) + 1
            self.write_status()

    async def run(self) -> str:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: setattr(self, "stop_requested", True))
        proc = writer = None
        outcome, err = "done", None
        try:
            proc, reader, writer = await self.start_gate()
            await self.trade(reader, writer)
            if self.stop_requested or self.control.state == "stopped":
                outcome = "stopped"
        except Exception as e:   # reported in the status file, never raised into a supervisor
            outcome, err = "failed", f"{type(e).__name__}: {e}"
        finally:
            if writer is not None:
                writer.close()
            if proc is not None and proc.returncode is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), 10)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            self.status.update(state=outcome, error=err, ended_at=_now())
            self.write_status(True)
        return outcome


def summarize(run_dir: Path) -> str:
    """Read-only summary of a finished run, from its log, with the same auditor the dashboard uses."""
    from auditor.replay import audit
    from core.money import fmt
    from core.rules import load_rules
    m = read_json(run_dir / "manifest.json")
    a = audit(run_dir / "gate.log", load_rules(run_dir / "rules.yaml"))
    rej = ", ".join(f"{k} {v}" for k, v in sorted(a.rejects.items(), key=lambda kv: -kv[1])) or "none"
    why = f" (requested {m['requested_provider']}: {m['fallback_reason']})" if m["fallback_reason"] else ""
    return "\n".join([
        f"run {m['run_id']}  SIMULATED, no real orders or money",
        f"  dir        {run_dir}",
        f"  seed       {m['seed']}   profile {m['profile']}   provider {m['provider']}{why}",
        f"  orders     {a.events} logged: {a.accepted} accepted, {a.events - a.accepted} rejected ({rej})",
        f"  realized   {fmt(a.book.realized_net)} (FIFO, fees included, simulated fills at the order price)",
        f"  chain      {'OK' if a.chain_error is None else 'BROKEN: ' + str(a.chain_error)}   findings {len(a.findings)}",
        "  Pacing is a setting, not a measurement: this is not a benchmark.",
    ])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sim", description=__doc__.split("\n\n")[0])
    p.add_argument("--run-dir", help="a run directory prepared by create_run (used by the API)")
    p.add_argument("--profile", default="disciplined", choices=PROFILES)
    p.add_argument("--seed", type=int, help="default: random (os.urandom); the same seed replays the same order stream")
    p.add_argument("--provider", default="auto", choices=PROVIDERS)
    p.add_argument("--symbols", help="comma separated; default: the 8 built-in symbols")
    p.add_argument("--interval", default="1d", choices=INTERVALS)
    p.add_argument("--speed", type=float, default=DEFAULT_SPEED, help=f"orders per second, {MIN_SPEED:g} to {MAX_SPEED:g}")
    p.add_argument("--orders", type=int, default=300, help="stop after this many orders")
    p.add_argument("--snapshot", help="reuse an earlier run's market_snapshot.json (exact replay with live prices)")
    p.add_argument("--runs-dir", default=str(ROOT / "runs"))
    p.add_argument("--rules", default=str(ROOT / "rules.yaml"))
    p.add_argument("--quiet", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.run_dir:
            d = Path(a.run_dir).resolve()
            if not RUN_ID_RE.match(d.name):
                raise RunError("not a run directory")
        else:
            d = create_run(a.runs_dir, base_rules=a.rules, root=ROOT, profile=a.profile, seed=a.seed, provider=a.provider,
                           symbols=a.symbols.split(",") if a.symbols else None, interval=a.interval, speed=a.speed,
                           max_orders=a.orders, snapshot_file=a.snapshot)
        runner = Runner(d)
    except RunError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    outcome = asyncio.run(runner.run())
    if not a.quiet:
        print(summarize(d) if (d / "gate.log").exists() else f"run {d.name}: {outcome}")
        if runner.status["error"]:
            print(f"  error: {runner.status['error']}", file=sys.stderr)
    return 0 if outcome != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
