"""Process-crash recovery. These tests kill the gate PROCESS with SIGKILL (and simulate a torn tail by
truncating the file). They do NOT test power loss or an OS crash: the kernel keeps everything that
was write()n when a process dies, so fsync is not exercised by a process kill. See docs/LIMITATIONS.md.
"""

import asyncio
import hashlib
import json
import os
import random
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import orjson
import pytest

from auditor.replay import audit
from core.logio import ChainError, repair_torn_tail, scan
from gate.engine import Gate
from tests.helpers import RULES, counter_clock, line, run, run_gate

ROOT = Path(__file__).resolve().parents[1]
CONNS, PER_CONN = 4, 100


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_gate(log, policy, out):
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "gate", "--rules", "rules.yaml", "--log", str(log),
                             "--port", str(port), "--fsync", policy], cwd=ROOT,
                            stdout=open(out, "w"), stderr=subprocess.STDOUT)
    deadline = time.time() + 90  # waits for "listening"; generous so a CPU-starved machine does not fail the test
    while "listening" not in open(out).read():
        assert proc.poll() is None, "gate died at startup: " + open(out).read()
        assert time.time() < deadline, "gate did not start"
        time.sleep(0.02)
    return proc, port


def stop_gate(proc, out):
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=20)
    return open(out).read()


async def pipelined(port, streams, proc=None, kill_after=None):
    """Send each stream pipelined on its own connection; SIGKILL `proc` after `kill_after` replies.
    Every reply that arrives is an acknowledgement the gate really sent, so all are returned."""
    replies = []

    async def one(lines):
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(b"".join(lines))
            await w.drain()
            for _ in lines:
                raw = await r.readline()
                if not raw:
                    return
                replies.append(orjson.loads(raw))
                if kill_after is not None and len(replies) == kill_after:
                    os.kill(proc.pid, signal.SIGKILL)
        except (ConnectionError, asyncio.IncompleteReadError):
            return

    await asyncio.gather(*(one(s) for s in streams))
    return replies


def streams(prefix, conns, per_conn):
    # BUY/SELL one share each: positions stay inside every limit whatever the interleaving
    return [[line(f"{prefix}{c}-{i}", "BUY" if i % 2 == 0 else "SELL", "AAPL", 1, 19000)
             for i in range(per_conn)] for c in range(conns)]


@pytest.mark.parametrize("policy", ["per_order", "batched"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_sigkill_loses_no_acknowledged_order(tmp_path, policy, seed):
    log, out = tmp_path / "g.log", tmp_path / "gate.out"
    first = streams("K", CONNS, PER_CONN)
    total = CONNS * PER_CONN
    kill_after = random.Random(seed).randint(20, total // 2)  # a random point early in the stream

    proc, port = start_gate(log, policy, out)
    acked = asyncio.run(pipelined(port, first, proc, kill_after))
    proc.wait(timeout=20)
    assert proc.returncode == -signal.SIGKILL and acked, "the gate must have been killed by SIGKILL"

    # restart on the same log; a torn tail (possible on some kernels if the kill lands mid-write) is
    # repaired at startup, everything else must verify as is
    proc2, port2 = start_gate(log, policy, tmp_path / "gate2.out")
    resent = asyncio.run(pipelined(port2, first + streams("N", 1, 20)))
    final_out = stop_gate(proc2, tmp_path / "gate2.out")

    events = list(scan(log))  # 1. the chain verifies (raises ChainError otherwise)
    by_seq = {e.seq: e for e in events}
    for a in acked:  # 2. every acknowledged order is in the log, unchanged
        e = by_seq[a["seq"]]
        assert (e.client_order_id, e.hash, e.decision, e.reason_code) == (
            a["client_order_id"], a["hash"], a["decision"], a["reason_code"])
    ids = [e.client_order_id for e in events]  # 3. no duplicated id, and every sent order is logged once
    assert len(ids) == len(set(ids)) == total + 20
    again = {r["client_order_id"]: r for r in resent}
    for a in acked:  # idempotency across the crash: the original decision comes back, flagged duplicate
        r = again[a["client_order_id"]]
        assert r["duplicate"] is True and (r["seq"], r["hash"]) == (a["seq"], a["hash"])
    assert not any("error" in r for r in resent)
    # 4. replay gives the same state hash as the restarted gate
    state = final_out.split("state_hash=")[1].split()[0]
    assert audit(log, RULES).book.state_hash() == state and audit(log, RULES).findings == []


def make_log(path, n=10):
    run(run_gate(path, [line(f"T{i}", "BUY" if i % 2 == 0 else "SELL", "AAPL", 1, 19000) for i in range(n)]))


def test_torn_tail_is_truncated_kept_and_noted(tmp_path):
    p = tmp_path / "g.log"
    make_log(p)
    data = p.read_bytes()
    p.write_bytes(data[:-15])  # the last line is now cut mid-way: no trailing newline
    torn_copy = tmp_path / "torn_copy.log"
    torn_copy.write_bytes(p.read_bytes())
    removed = data[:-15].rsplit(b"\n", 1)[1]

    with pytest.raises(ChainError, match="partial last line"):  # the auditor reports, never repairs
        list(scan(torn_copy))
    assert audit(torn_copy, RULES).chain_error.seq == 10 and torn_copy.read_bytes() == p.read_bytes()

    async def restart():
        g = Gate(RULES, p, "per_order", clock=counter_clock(1))
        await g.start()
        r = orjson.loads(await (await g.submit(line("NEW", "BUY", "AAPL", 1, 19000))))
        await g.stop()
        return g, r

    g, reply = run(restart())
    note = g.recovery_note
    assert g.book.last_seq == 10 and reply["seq"] == 10  # 9 intact events, the new one becomes seq 10
    assert note["action"] == "truncated_torn_tail" and note["bytes_removed"] == len(removed)
    assert note["removed_sha256"] == hashlib.sha256(removed).hexdigest()
    assert (tmp_path / note["removed_saved_as"]).read_bytes() == removed  # the bytes are kept
    logged = [json.loads(l) for l in (tmp_path / "g.log.recovery.jsonl").read_text().splitlines()]
    assert logged == [note]
    assert [e.seq for e in scan(p)] == list(range(1, 11))


@pytest.mark.parametrize("tail", [b"{\"seq\":11,\"garb", b"x"])
def test_garbage_after_the_last_newline_is_also_a_torn_tail(tmp_path, tail):
    p = tmp_path / "g.log"
    make_log(p, 5)
    good = p.read_bytes()
    p.write_bytes(good + tail)
    assert repair_torn_tail(p)["bytes_removed"] == len(tail) and p.read_bytes() == good
    assert repair_torn_tail(p) is None  # nothing left to repair


def test_only_a_missing_newline_is_repaired_other_corruption_refuses_to_start(tmp_path):
    p = tmp_path / "g.log"
    make_log(p, 6)
    rows = p.read_bytes().splitlines(keepends=True)
    rows[2] = rows[2].replace(b'"qty":1', b'"qty":2')  # an edit in the middle, newline intact
    p.write_bytes(b"".join(rows))
    before = p.read_bytes()
    r = subprocess.run([sys.executable, "-m", "gate", "--rules", "rules.yaml", "--log", str(p),
                        "--port", str(free_port())], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and "refusing to start" in (r.stdout + r.stderr) and "seq 3" in (r.stdout + r.stderr)
    assert p.read_bytes() == before and not list(tmp_path.glob("*.torn.*"))  # nothing touched


def test_log_that_is_only_a_torn_line_becomes_empty(tmp_path):
    p = tmp_path / "g.log"
    p.write_bytes(b"{\"seq\":1")
    assert repair_torn_tail(p)["kept_bytes"] == 0 and p.read_bytes() == b""
