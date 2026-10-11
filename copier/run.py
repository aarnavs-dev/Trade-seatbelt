"""A follow run is runs/<id>/: manifest.json, rules.yaml (hashed in the manifest), feed.jsonl, copier.log, gate.log.
Run-id, atomic JSON and hashing helpers come from sim.runfiles (Tier 1); the repo's rules.yaml is only ever read."""

import asyncio
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from copier.config import FollowerConfig, validate_overrides
from copier.data import ReplayData
from core.rules import Rules
from sim.runfiles import git_info, new_run_id, run_dir_for, sha256_file, write_json_atomic

ROOT = Path(__file__).resolve().parent.parent
KIND = "follow"


def build_rules(base_rules, anchors: dict, overrides: dict) -> tuple[str, Rules]:
    """Limits from the repo's rules.yaml (read-only) + validated overrides + a `symbols` block of previous-close anchors."""
    d = yaml.safe_load(Path(base_rules).read_text())
    for k, v in validate_overrides(overrides).items():
        d[k] = {**d[k], **v} if isinstance(v, dict) else v
    d["symbols"] = {s: {"reference_price": c} for s, c in sorted(anchors.items())}
    rules = Rules.model_validate(d)
    note = ", ".join(f"{k}={v}" for k, v in sorted(overrides.items())) or "none"
    text = ("# Generated for this follow run. Limits are copied from the repo's rules.yaml; overrides: " + note + ".\n"
            "# `symbols`: reference_price = previous session's last-bar close (not an official close), in cents.\n"
            + yaml.safe_dump(d, sort_keys=False))
    return text, rules


def create_follow_run(runs_dir, *, base_rules, data: ReplayData, cfg: FollowerConfig, overrides=None, leader="orb",
                      leader_qty=10, root=ROOT, now=None, feed=None) -> Path:
    overrides = overrides or {}
    text, rules = build_rules(base_rules, data.anchors, overrides)
    d = run_dir_for(runs_dir, new_run_id(now))
    d.mkdir(parents=True, exist_ok=False)
    (d / "rules.yaml").write_text(text)
    write_json_atomic(d / "manifest.json", {
        "run_id": d.name, "kind": KIND, "simulated": True, "paper_only": True,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": data.describe(), "data_label": data.label, "provider": data.provider,
        "feed": feed or {"source": "local feed"},
        "leader": leader, "leader_qty": leader_qty, "follower": cfg.to_dict(), "limit_overrides": overrides,
        "symbols": sorted(data.anchors), "position_limit": rules.max_position_per_symbol, "fsync": "batched",
        "rules_sha256": sha256_file(d / "rules.yaml"), "base_rules_sha256": sha256_file(base_rules),
        "fills": "paper: an accepted order is a simulated full fill at its price",
        "git": git_info(Path(root)),
    })
    return d


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_gate(run_dir: Path, timeout=20.0):
    """Start `python -m gate` for this run with its own rules.yaml and the existing (batched) policy.
    Returns (process, port) once it accepts connections."""
    port = free_port()
    out = open(run_dir / "gate.out", "ab")
    proc = subprocess.Popen([sys.executable, "-m", "gate", "--rules", str(run_dir / "rules.yaml"),
                             "--log", str(run_dir / "gate.log"), "--port", str(port), "--fsync", "batched"],
                            cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
    out.close()
    end = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f"gate exited early with code {proc.returncode} (see {run_dir / 'gate.out'})")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return proc, port
        except OSError:
            if time.monotonic() > end:
                proc.kill()
                raise RuntimeError("the gate did not start in time") from None
            time.sleep(0.05)


def stop_gate(proc, wait=10.0):
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(wait)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
