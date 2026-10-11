"""HTTP routes for follower runs. A run is started as a subprocess (`python -m copier follow`) that owns its own gate; this
module only starts and stops that process and READS runs/<id>/ files. It never writes a log and is never on the order path.
The only identifiers a client sends are a run id and a signal number; every path is built here."""

import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from api import tail
from auditor.explain import explain_copier_log, explain_log
from copier.config import ConfigError, validate_overrides
from copier.rows import COPIED, CLIP, REJECTED, STOPPED, build_rows
from core.logio import ChainError
from core.rules import load_rules
from sim.runfiles import RUN_ID_RE, RunError, read_json, run_dir_for

START_TIMEOUT_S = 90.0
FINAL_STATES = ("done", "failed", "stopped")


class NewFollow(BaseModel):
    model_config = ConfigDict(extra="forbid")        # a stray "path", "rules" or "runs_dir" is a 422, never used
    feed: Literal["local", "chain"] = "local"
    leader: Literal["orb", "momentum", "meanrev"] = "momentum"
    data: Literal["auto", "fixture"] = "auto"
    limits: dict[str, Any] | None = None             # checked by copier.config.validate_overrides (its message is shown to the user)


def _err(status: int, error: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": error, "message": message, **extra}, status_code=status)


def _alive(pid) -> bool:
    try:
        os.kill(pid, 0)
        return isinstance(pid, int) and pid > 0
    except (OSError, TypeError):
        return False


class Follows:
    def __init__(self, runs_dir, root, base_rules, pace_ms):
        self.runs_dir, self.root, self.base_rules, self.pace_ms = Path(runs_dir), Path(root), Path(base_rules), pace_ms
        self.procs: dict[str, subprocess.Popen] = {}

    def dir(self, run_id) -> Path | None:
        try:
            d = run_dir_for(self.runs_dir, run_id)
        except RunError:
            return None
        m = read_json(d / "manifest.json")
        return d if m and m.get("kind") == "follow" else None

    def state(self, d: Path) -> str:
        proc = self.procs.get(d.name)
        st = read_json(d / "sim.status.json") or {}
        s = st.get("state", "starting")
        if s in FINAL_STATES:
            return s
        if proc is not None:
            return s if proc.poll() is None else ("done" if proc.returncode == 0 and (d / "follow.summary.json").exists() else "stopped" if proc.returncode in (143, -15) else "failed")
        return s if _alive(st.get("pid")) else "interrupted"

    def running(self) -> str | None:
        return next((i for i, p in self.procs.items() if p.poll() is None), None)

    def start(self, body: NewFollow) -> str:
        limits = validate_overrides(body.limits or {})
        if (cur := self.running()) is not None:
            raise RuntimeError(cur)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, "-m", "copier", "follow", "--announce", "--feed", body.feed, "--leader", body.leader,
               "--scale", "1/1", "--runs-dir", str(self.runs_dir), "--rules", str(self.base_rules), "--pace-ms", str(self.pace_ms)]
        for k, v in limits.items():
            for kk, vv in (v.items() if isinstance(v, dict) else [(None, v)]):
                cmd += ["--limit", f"{k}.{kk}={vv}" if kk else f"{k}={vv}"]
        if body.data == "fixture":
            cmd.append("--offline")
        out_path = self.runs_dir / f".follow-start-{os.getpid()}-{time.monotonic_ns()}.out"
        with open(out_path, "wb") as out:
            proc = subprocess.Popen(cmd, cwd=self.root, stdout=out, stderr=subprocess.STDOUT)
        end = time.monotonic() + START_TIMEOUT_S
        try:
            while time.monotonic() < end:
                m = re.search(rb"^RUN (\d{8}-\d{6}-[0-9a-f]{4})$", out_path.read_bytes(), re.M)
                if m:
                    run_id = m.group(1).decode()
                    self.procs[run_id] = proc
                    out_path.replace(self.runs_dir / run_id / "follow.out")
                    return run_id
                if proc.poll() is not None:
                    tail_txt = out_path.read_bytes()[-300:].decode(errors="replace")
                    raise RunError(f"the follower exited before it started: {tail_txt.strip()}")
                time.sleep(0.1)
            raise RunError("the follower did not start in time")
        except BaseException:
            if proc.poll() is None:
                proc.terminate()
            raise

    def stop(self, run_id) -> bool:
        p = self.procs.get(run_id)
        if p is None or p.poll() is not None:
            return False
        p.terminate()
        return True

    def shutdown(self):
        for p in self.procs.values():
            if p.poll() is None:
                p.terminate()
        for p in self.procs.values():
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


def summarize(f: Follows, d: Path) -> dict:
    m = read_json(d / "manifest.json") or {}
    rows, feed_n, _ = build_rows(d)
    stopped, rejected = {}, {}
    for r in rows:
        if r["status"] == STOPPED:
            stopped[r["reason_code"]] = stopped.get(r["reason_code"], 0) + 1
        elif r["status"] == REJECTED:
            rejected[r["reason_code"]] = rejected.get(r["reason_code"], 0) + 1
    data, feed = m.get("data", {}), m.get("feed", {})
    notes = []
    if data.get("fallback_reason"):
        notes.append(f"Using the SYNTHETIC FIXTURE because real data could not be used ({data['fallback_reason']}).")
    if feed.get("requested") == "chain" and feed.get("source") != "chain":
        notes.append(f"Using {feed.get('source')} because the chain feed could not be read ({feed.get('why') or 'see the manifest'}).")
    closed = stopped.get("MARKET_CLOSED", 0)
    if closed:
        notes.append(f"The market was closed for {closed} signal(s); the copier stopped them.")
    rules = load_rules(d / "rules.yaml")
    return {
        "run_id": d.name, "state": f.state(d), "simulated": True, "paper_only": True, "total_in_feed": feed_n,
        "seen": len(rows), "copied": sum(1 for r in rows if r["status"] in (COPIED, CLIP)),
        "clipped": sum(1 for r in rows if r["status"] == CLIP), "stopped": sum(stopped.values()), "stopped_by_reason": stopped,
        "rejected": sum(rejected.values()), "rejected_by_reason": rejected, "notes": notes,
        "data": data, "data_label": m.get("data_label"), "feed": feed, "leader": m.get("leader"), "follower": m.get("follower"),
        "limit_overrides": m.get("limit_overrides", {}),
        "limits": {"max_position_per_symbol": rules.max_position_per_symbol, "max_daily_loss": rules.max_daily_loss,
                   "price_band_pct": rules.price_band_pct, "cooldown_after_losses": rules.loss_streak_cooldown.losses,
                   "cooldown_seconds": rules.loss_streak_cooldown.cooldown_seconds},
        "manifest": m,
    }


def explain_row(d: Path, row: dict) -> dict:
    base = {"n": row["n"], "status": row["status"], "signal_id": row["signal_id"], "reason_code": row["reason_code"],
            "leader_signal": row["leader_signal"], "explorer_url": row["explorer_url"], "order": None, "rule": None,
            "protects_against": None, "limit": None, "value_before": None, "value_if_accepted": None, "detail": row["reason_text"],
            "cause_text": None}
    if row["status"] == REJECTED:
        e = next(x for x in explain_log(d / "gate.log", load_rules(d / "rules.yaml")) if x["seq"] == row["gate_seq"])
        return {**base, **{k: e.get(k) for k in ("order", "rule", "protects_against", "limit", "value_before", "value_if_accepted", "detail", "cause_text")}}
    if row["status"] in (STOPPED, CLIP):
        e = next(x for x in explain_copier_log(d / "copier.log") if x["seq"] == row["ledger_seq"])
        return {**base, "rule": e["reason_code"], "protects_against": e["protects_against"], "detail": e["detail"]}
    return {**base, "rule": "all rules", "detail": row["reason_text"],
            "order": f"{row['side']} {row['qty']} {row['symbol']} @ {row['price']} cents"}


def build_router(f: Follows, sse_poll: float) -> APIRouter:
    r = APIRouter(prefix="/follow")

    def find(run_id):
        return f.dir(run_id)

    @r.post("/run", status_code=201)
    async def start(body: NewFollow):
        try:
            run_id = await run_in_threadpool(f.start, body)
        except ConfigError as e:
            return _err(422, "bad_limit", str(e))
        except RunError as e:
            return _err(500, "start_failed", str(e))
        except RuntimeError as e:
            return _err(409, "follow_running", f"a follow run is already running: {e}", active=str(e))
        return {"run": run_id, "stream": f"/follow/{run_id}/stream", "simulated": True}

    @r.get("/{run}/summary")
    def summary(run: str):
        d = find(run)
        return summarize(f, d) if d else _err(404, "no_such_run", "no follow run with that id")

    @r.post("/{run}/stop")
    def stop(run: str):
        if not find(run):
            return _err(404, "no_such_run", "no follow run with that id")
        return {"stopped": f.stop(run), "state": f.state(find(run))}

    @r.get("/{run}/explain/{n}")
    def explain(run: str, n: int):
        d = find(run)
        if not d:
            return _err(404, "no_such_run", "no follow run with that id")
        row = next((x for x in build_rows(d)[0] if x["n"] == n), None)
        if row is None:
            return _err(404, "no_such_signal", f"no resolved signal number {n} in this run")
        try:
            return explain_row(d, row)
        except ChainError as e:
            return _err(409, "log_not_verified", f"{e}; an explanation of a damaged log would be worthless")

    @r.get("/{run}/stream")
    async def stream(run: str, request: Request):
        d = find(run)
        if not d:
            return _err(404, "no_such_run", "no follow run with that id")
        hdr = request.headers.get("last-event-id", "")
        after = int(hdr) if hdr.isdigit() else 0

        async def gen():
            nonlocal after
            idle = 0.0
            while True:
                if await request.is_disconnected():
                    return
                state = f.state(d)
                rows, total, _ = await run_in_threadpool(build_rows, d)
                new = [x for x in rows if x["n"] > after]
                for x in new:
                    after = x["n"]
                    yield tail.sse("signal", x, x["n"])
                if new:
                    idle = 0.0
                if state in FINAL_STATES + ("interrupted",) and not new:
                    yield tail.sse("end", {"state": state, "seen": len(rows), "total": total})
                    return
                idle += sse_poll
                if idle >= 15:
                    idle = 0.0
                    yield b": ping\n\n"
                await asyncio.sleep(sse_poll)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return r
