"""Simulation runs: create, list, select, control, stop. The client names a run by id; every path is built here.

A run's simulator is its own process (`python -m sim --run-dir ...`), which starts the run's gate. This module never
touches the order path and never writes a log: it writes control.json (state/speed) and reads the rest."""

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sim import MAX_SPEED, MIN_SPEED
from sim.runfiles import RUN_ID_RE, RunError, create_run, read_json, run_dir_for, write_json_atomic

LIVE_STATES = ("starting", "running", "paused")
STALE_S = 10.0
LIST_CAP = 200


def _reap_orphan_gate(d: Path, st: dict) -> None:
    """The simulator died without stopping its gate (kill -9). Stop that gate, but only if the pid still is
    `python -m gate` for THIS run's log (a recycled pid is left alone)."""
    pid = st.get("gate_pid")
    if not isinstance(pid, int) or pid <= 1:
        return
    try:
        cmd = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return
    if " -m gate" in cmd and str(d / "gate.log") in cmd:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


class NoSuchRun(Exception):
    pass


class RunConflict(Exception):
    pass


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class RunManager:
    def __init__(self, runs_dir, root, base_rules, *, providers=None, env=None, python=sys.executable):
        self.runs_dir, self.root, self.base_rules = Path(runs_dir), Path(root), Path(base_rules)
        self.providers, self.env, self.python = providers, env, python
        self.procs: dict[str, subprocess.Popen] = {}
        self.selected: str | None = None

    # ---- lookup
    def dir(self, run_id: str) -> Path:
        try:
            d = run_dir_for(self.runs_dir, run_id)
        except RunError:
            raise NoSuchRun(run_id) from None
        if not (d / "manifest.json").is_file():
            raise NoSuchRun(run_id)
        return d

    def log_path(self, run_id: str) -> Path:
        return self.dir(run_id) / "gate.log"

    def info(self, run_id: str) -> dict:
        d = self.dir(run_id)
        m, st = read_json(d / "manifest.json") or {}, read_json(d / "sim.status.json")
        proc = self.procs.get(run_id)
        if st is None:
            alive = proc is not None and proc.poll() is None
            state = "starting" if alive else "interrupted"
            st = {}
        else:
            state = st.get("state", "unknown")
            if state in LIVE_STATES:
                if proc is not None:
                    alive = proc.poll() is None
                else:
                    alive = _pid_alive(st.get("pid"))
                    try:
                        alive = alive and time.time() - (d / "sim.status.json").stat().st_mtime < STALE_S
                    except OSError:
                        alive = False
                if not alive:
                    state = "interrupted"   # the simulator process is gone without a final status
                    _reap_orphan_gate(d, st)
        ctl = read_json(d / "control.json") or {}
        return {
            "id": run_id, "state": state, "selected": run_id == self.selected,
            "simulated": True, "started_at": m.get("started_at"), "seed": m.get("seed"), "profile": m.get("profile"),
            "provider": m.get("provider"), "requested_provider": m.get("requested_provider"),
            "fallback_reason": m.get("fallback_reason"), "symbols": m.get("symbols"), "interval": m.get("interval"),
            "max_orders": m.get("max_orders"), "speed": ctl.get("speed", st.get("speed", m.get("speed"))),
            "sent": st.get("sent", 0), "accepted": st.get("accepted", 0), "rejected": st.get("rejected", 0),
            "errors": st.get("errors", 0), "rejects_by_reason": st.get("rejects_by_reason", {}),
            "error": st.get("error"), "ended_at": st.get("ended_at"),
            "manifest": m,
        }

    def ids(self) -> list[str]:
        if not self.runs_dir.is_dir():
            return []
        names = sorted((n.name for n in self.runs_dir.iterdir() if RUN_ID_RE.match(n.name) and (n / "manifest.json").is_file()),
                       reverse=True)
        return names[:LIST_CAP]

    def list(self) -> dict:
        ids = self.ids()
        if self.selected not in ids:
            self.selected = ids[0] if ids else None
        runs = [self.info(i) for i in ids]
        active = next((r["id"] for r in runs if r["state"] in LIVE_STATES), None)
        return {"runs": runs, "selected": self.selected, "active": active}

    def active(self) -> str | None:
        return self.list()["active"]

    def select(self, run_id: str) -> dict:
        self.dir(run_id)
        self.selected = run_id
        return self.info(run_id)

    # ---- lifecycle
    def create(self, **kw) -> dict:
        """Blocking (fetches the snapshot once); the route runs it in a thread."""
        if (cur := self.active()) is not None:
            raise RunConflict(cur)
        d = create_run(self.runs_dir, base_rules=self.base_rules, root=self.root, env=self.env, providers=self.providers, **kw)
        out = open(d / "sim.out", "wb")
        self.procs[d.name] = subprocess.Popen([self.python, "-m", "sim", "--run-dir", str(d), "--quiet"], cwd=self.root,
                                              stdout=out, stderr=subprocess.STDOUT)
        out.close()
        self.selected = d.name
        return self.info(d.name)

    def _write_control(self, run_id: str, **changes) -> dict:
        d = self.dir(run_id)
        info = self.info(run_id)
        if info["state"] not in LIVE_STATES:
            raise RunConflict(f"run is {info['state']}")
        ctl = read_json(d / "control.json") or {"state": "running", "speed": info["speed"]}
        ctl.update(changes)
        ctl["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        write_json_atomic(d / "control.json", ctl)
        return self.info(run_id)

    def pause(self, run_id):
        if self.info(run_id)["state"] == "paused":
            return self.info(run_id)
        return self._write_control(run_id, state="paused")

    def resume(self, run_id):
        return self._write_control(run_id, state="running")

    def stop(self, run_id):
        return self._write_control(run_id, state="stopped")

    def set_speed(self, run_id, speed: float):
        if not MIN_SPEED <= speed <= MAX_SPEED:
            raise RunError(f"speed must be between {MIN_SPEED:g} and {MAX_SPEED:g}")
        return self._write_control(run_id, speed=float(speed))

    def shutdown(self, wait_s: float = 12.0) -> None:
        """API is stopping: ask our simulators to stop, then make sure they and their gates are gone."""
        live = [(i, p) for i, p in self.procs.items() if p.poll() is None]
        for i, _ in live:
            try:
                self._write_control(i, state="stopped")
            except (NoSuchRun, RunConflict):
                pass
        end = time.monotonic() + wait_s
        for _, p in live:
            try:
                p.wait(max(0.1, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
