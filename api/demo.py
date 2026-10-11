"""Run the existing demo flow (fixture orders -> a real gate process -> hash-chained log) in a fresh temp dir.

The gate is its own subprocess (`python -m gate`), exactly as `make demo` starts it; this module is only a TCP client
of it, like `client/`. The API process never sits on the order path of a real gate."""

import asyncio
import shutil
import signal
import socket
import sys
import tempfile
import time
from pathlib import Path

import orjson

GATE_START_TIMEOUT_S = 20.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DemoRunner:
    def __init__(self, root: Path, rules: Path, orders: Path, on_log):
        self.root, self.rules, self.orders, self.on_log = root, rules, orders, on_log
        self.dir: Path | None = None
        self.task: asyncio.Task | None = None
        self.status = {"state": "idle", "run": 0, "sent": 0, "total": 0, "error": None}

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, delay_ms: int) -> dict:
        if self.running:
            raise RuntimeError("a demo run is already in progress")
        lines = [l.rstrip(b"\n") + b"\n" for l in self.orders.read_bytes().splitlines() if l.strip()]
        self.cleanup()
        self.dir = Path(tempfile.mkdtemp(prefix="ts-demo-"))
        log = self.dir / "gate.log"
        self.status = {"state": "running", "run": self.status["run"] + 1, "sent": 0, "total": len(lines), "error": None}
        self.on_log(log)
        self.task = asyncio.create_task(self._run(log, lines, delay_ms / 1000))
        return dict(self.status)

    def cleanup(self) -> None:
        if self.dir is not None:  # only ever removes the temp dir this runner made
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None

    async def _run(self, log: Path, lines: list[bytes], delay: float) -> None:
        port, proc = _free_port(), None
        outcome: dict = {"state": "done"}  # applied only after the gate has stopped, so "done" means the log is final and a new run is accepted
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "gate", "--rules", str(self.rules), "--log", str(log), "--port", str(port),
                "--fsync", "batched", cwd=self.root, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            reader, writer = await self._connect(port, proc)
            for line in lines:
                writer.write(line)
                await writer.drain()
                resp = orjson.loads(await reader.readline())
                if "error" in resp:
                    raise RuntimeError(f"gate refused an order: {resp['error']}")
                self.status["sent"] += 1
                if delay:
                    await asyncio.sleep(delay)
            writer.close()
        except asyncio.CancelledError:
            self.status.update(state="failed", error="cancelled")
            raise
        except Exception as e:  # reported in /demo/status, never raised into the server
            outcome = {"state": "failed", "error": f"{type(e).__name__}: {e}"}
        finally:
            if proc is not None and proc.returncode is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            if self.status["state"] == "running":
                self.status.update(outcome)

    @staticmethod
    async def _connect(port: int, proc):
        deadline = time.monotonic() + GATE_START_TIMEOUT_S
        while True:
            if proc.returncode is not None:
                raise RuntimeError(f"gate exited early with code {proc.returncode}")
            try:
                return await asyncio.open_connection("127.0.0.1", port)
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError(f"the gate did not start in time (no connection after {GATE_START_TIMEOUT_S:g} s)") from None
                await asyncio.sleep(0.05)
