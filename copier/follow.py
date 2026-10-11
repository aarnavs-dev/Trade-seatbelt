"""Orchestration: feed.jsonl in a run dir -> Copier -> gate. `run_follower` is also what a restart calls."""

import time
from pathlib import Path

import orjson

from copier.client import GateClient
from copier.config import FollowerConfig
from copier.copier import Copier
from copier.feed import read_local
from copier.ledger import CopierLog
from sim.runfiles import read_json, write_json_atomic


def run_follower(run_dir, port, *, pace_ms=0) -> dict:
    """Process the whole feed.jsonl of `run_dir` against the gate on `port`. Safe to run again after a crash: every
    order id is derived from its signal id (the gate answers a repeat with the original decision) and copier.log
    keeps only lines it has not got yet. `pace_ms` is a test hook that sleeps between signals."""
    d = Path(run_dir)
    m = read_json(d / "manifest.json")
    from core.rules import load_rules
    rules = load_rules(d / "rules.yaml")
    cfg = FollowerConfig.from_dict(m["follower"])
    log, client = CopierLog(d / "copier.log"), GateClient(port)
    try:
        cp = Copier(cfg, rules.symbols, client.submit, log)
        for inc in read_local(d / "feed.jsonl"):
            cp.process(inc)
            if pace_ms:
                time.sleep(pace_ms / 1000)
        summary = cp.summary()
    finally:
        client.close()
        log.close()
    write_json_atomic(d / "follow.summary.json", summary)
    return summary


def write_status(run_dir, state, summary=None):
    """Same file the runs API reads for simulator runs, so a follow run is listed with a state."""
    import os
    from datetime import datetime, timezone
    s = summary or {}
    write_json_atomic(Path(run_dir) / "sim.status.json", {
        "state": state, "pid": os.getpid(), "sent": s.get("sent", 0), "accepted": s.get("accepted", 0),
        "rejected": sum(s.get("rejected", {}).values()), "errors": 0, "rejects_by_reason": s.get("rejected", {}),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "error": None,
        "ended_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds") if state != "running" else None,
    })
