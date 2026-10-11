"""A reply must never reach a client before the fsync that covers its event completed."""

import asyncio
import os
import sys
import time

import orjson
import pytest

import core.logio as logio
from core.logio import LogWriter, scan
from gate.engine import Gate, GateClosed, _resp
from gate.server import start_server
from tests.helpers import RULES, counter_clock, line

CONNS, PER_CONN = 4, 50


def slow_fsync(trace, delay=0.01):
    real = os.fsync

    def fake(fd):
        time.sleep(delay)  # a slow disk; blocks like a real fsync
        real(fd)
        trace.append(("fsync_done", os.fstat(fd).st_size))
    return fake


def violations(trace, ends):
    """Replies that arrived while no completed fsync covered their event's bytes."""
    bad, durable = [], 0
    for kind, v in trace:
        if kind == "fsync_done":
            durable = max(durable, v)
        elif durable < ends[v]:
            bad.append(v)
    return bad


async def run_clients(path, fsync, trace, commit=None, before_stop=None):
    gate = Gate(RULES, path, fsync, clock=counter_clock())
    if commit:
        gate._commit = commit.__get__(gate)
    await gate.start()
    server = await start_server(gate, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def client(c):
        r, w = await asyncio.open_connection("127.0.0.1", port)
        # BUY then SELL one share each: positions stay inside every limit
        w.write(b"".join(line(f"C{c}-{i}", "BUY" if i % 2 == 0 else "SELL", "AAPL", 1, 19000)
                         for i in range(PER_CONN)))
        await w.drain()
        for _ in range(PER_CONN):
            trace.append(("reply", orjson.loads(await r.readline())["seq"]))
        w.close()

    await asyncio.gather(*(client(c) for c in range(CONNS)))
    server.close()
    if before_stop:
        before_stop()
    await gate.stop()


def ends_of(path):
    pos, ends = 0, {}
    for raw, ev in zip(open(path, "rb").read().splitlines(keepends=True), scan(path)):
        pos += len(raw)
        ends[ev.seq] = pos
    return ends


@pytest.mark.parametrize("fsync", ["per_order", "batched"])
def test_no_reply_before_its_fsync(tmp_path, monkeypatch, fsync):
    trace = []
    monkeypatch.setattr(logio.os, "fsync", slow_fsync(trace))
    asyncio.run(run_clients(tmp_path / "g.log", fsync, trace))
    ends, n = ends_of(tmp_path / "g.log"), CONNS * PER_CONN
    assert len(ends) == n and sum(k == "reply" for k, _ in trace) == n
    assert violations(trace, ends) == []
    fsyncs = sum(k == "fsync_done" for k, _ in trace)
    if fsync == "per_order":
        assert fsyncs == n          # one fsync per event
    else:
        assert fsyncs < n           # group commit really happened


def test_the_check_catches_a_reply_sent_before_fsync(tmp_path, monkeypatch):
    """Negative control: a gate that answers first and syncs later (on another thread) must be flagged.

    In the real gate the ordering is structural: _commit is synchronous, so reply tasks cannot run
    until write+fsync returned. This variant breaks that on purpose, so the checker has to prove it
    can see a violation."""
    from concurrent.futures import ThreadPoolExecutor

    trace, pool = [], ThreadPoolExecutor(max_workers=1)  # one worker keeps log order intact
    monkeypatch.setattr(logio.os, "fsync", slow_fsync(trace))

    def reply_first(self, batch):
        outs, lines = [], []
        for raw, fut in batch:
            ev, resp = self._handle(raw)
            if ev is not None:
                lines.append(logio.event_line(ev))
                resp = _resp(ev, False)
            outs.append((fut, resp))
        for fut, resp in outs:
            fut.set_result(resp)  # BUG under test: answered before the write and fsync
        data = b"".join(lines)
        pool.submit(lambda: (time.sleep(0.05), self.writer.write(data)))

    asyncio.run(run_clients(tmp_path / "g.log", "batched", trace, commit=reply_first,
                            before_stop=lambda: pool.shutdown(wait=True)))
    assert violations(trace, ends_of(tmp_path / "g.log"))


def test_failed_fsync_means_no_acknowledgement(tmp_path, monkeypatch):
    def broken(fd):
        raise OSError("disk gone")
    monkeypatch.setattr(logio.os, "fsync", broken)

    async def main():
        gate = Gate(RULES, tmp_path / "g.log", "per_order", clock=counter_clock())
        await gate.start()
        fut = await gate.submit(line("X1", "BUY", "AAPL", 1, 19000))
        with pytest.raises(GateClosed):
            await fut
        with pytest.raises(GateClosed):  # and it stops taking orders
            await gate.submit(line("X2", "BUY", "AAPL", 1, 19000))
    asyncio.run(main())


@pytest.mark.skipif(sys.platform != "darwin", reason="F_FULLFSYNC is macOS only")
def test_full_fsync_uses_f_fullfsync_not_os_fsync(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(logio.os, "fsync", lambda fd: calls.append("os.fsync"))
    monkeypatch.setattr(logio.fcntl, "fcntl", lambda fd, cmd, *a: calls.append(("fcntl", cmd)))
    w = LogWriter(tmp_path / "l", "per_order", full_fsync=True)
    w.write(b"x\n")
    w.close()
    assert calls == [("fcntl", logio.fcntl.F_FULLFSYNC)]


@pytest.mark.skipif(sys.platform == "darwin", reason="only meaningful off macOS")
def test_full_fsync_refused_off_macos(tmp_path):
    with pytest.raises(ValueError):
        LogWriter(tmp_path / "l", "per_order", full_fsync=True)
