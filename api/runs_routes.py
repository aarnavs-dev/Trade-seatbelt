"""HTTP routes for simulation runs. Bodies are validated models; the only identifier a client sends is a run id."""

from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from api import tail
from api.runs import NoSuchRun, RunConflict, RunManager
from auditor.replay import audit
from auditor.report import build_report
from core.rules import load_rules
from sim import DEFAULT_MAX_ORDERS, DEFAULT_SPEED, MAX_SPEED, MIN_SPEED
from sim.runfiles import RunError


class NewRun(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a stray field such as "path" or "log" is a 422, not silently ignored
    profile: Literal["disciplined", "impulsive", "tilt"] = "disciplined"
    provider: Literal["auto", "yfinance", "alpaca", "offline"] = "auto"
    seed: int | None = Field(default=None, ge=0, lt=2 ** 64)
    symbols: list[str] | None = Field(default=None, max_length=12)
    interval: Literal["1d", "1h", "15m", "5m"] = "1d"
    speed: float = Field(default=DEFAULT_SPEED, ge=MIN_SPEED, le=MAX_SPEED)
    max_orders: int = Field(default=DEFAULT_MAX_ORDERS, ge=1, le=5_000_000)


class Speed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed: float = Field(ge=MIN_SPEED, le=MAX_SPEED)


def _err(status: int, error: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": error, "message": message, **extra}, status_code=status)


def build_router(runs: RunManager, sse_poll: float) -> APIRouter:
    r = APIRouter(prefix="/runs")
    report_cache: dict = {}

    def guard(fn):
        try:
            return fn()
        except NoSuchRun:
            return _err(404, "no_such_run", "no run with that id")
        except RunConflict as e:
            return _err(409, "conflict", f"not possible now: {e}", active=runs.active())
        except RunError as e:
            return _err(422, "bad_request", str(e))

    @r.get("")
    def list_runs():
        return runs.list()

    @r.post("", status_code=201)
    async def create(body: NewRun):
        return await run_in_threadpool(lambda: guard(lambda: runs.create(**body.model_dump())))

    @r.get("/{run_id}")
    def get_run(run_id: str):
        return guard(lambda: runs.info(run_id))

    @r.post("/{run_id}/select")
    def select(run_id: str):
        return guard(lambda: runs.select(run_id))

    @r.post("/{run_id}/pause")
    def pause(run_id: str):
        return guard(lambda: runs.pause(run_id))

    @r.post("/{run_id}/resume")
    def resume(run_id: str):
        return guard(lambda: runs.resume(run_id))

    @r.post("/{run_id}/stop")
    def stop(run_id: str):
        return guard(lambda: runs.stop(run_id))

    @r.post("/{run_id}/speed")
    def speed(run_id: str, body: Speed):
        return guard(lambda: runs.set_speed(run_id, body.speed))

    @r.get("/{run_id}/events/stream")
    async def stream(run_id: str, request: Request, tail_n: int = Query(500, alias="tail", ge=1, le=5000), follow: bool = True):
        try:
            log = runs.log_path(run_id)
        except NoSuchRun:
            return _err(404, "no_such_run", "no run with that id")
        hdr = request.headers.get("last-event-id", "")
        gen = tail.events(lambda: log, tail=tail_n, after_seq=int(hdr) if hdr.isdigit() else 0, follow=follow, poll=sse_poll)
        return StreamingResponse(gen, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @r.get("/{run_id}/report")
    def report(run_id: str):
        def build():
            d = runs.dir(run_id)
            log = d / "gate.log"
            key = (run_id, log.stat().st_size, log.stat().st_mtime_ns) if log.exists() else (run_id, 0, 0)
            if report_cache.get("key") != key:
                rules = load_rules(d / "rules.yaml")
                rep = build_report(audit(log, rules), run_id, None)
                rep["source"], rep["rules"] = run_id, rules.model_dump()
                report_cache.clear()
                report_cache.update(key=key, value=rep)
            return report_cache["value"]
        return guard(build)

    return r
