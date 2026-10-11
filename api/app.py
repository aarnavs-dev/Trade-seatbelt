"""Thin read-only API for the dashboard. Never on the order hot path: it reads the log, it does not write it.

The exceptions to "read-only" start OTHER processes and never write a log themselves: POST /demo/run (a throwaway gate in a
fresh temp dir) and POST /runs (a simulator, which starts that run's own gate under runs/<id>/). Clients choose a named source
("demo" or "recon") or a run id, never a file path."""

import asyncio
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from api import bench as bench_mod
from api import invariants as inv_mod
from api import tail
from api.demo import DemoRunner
from api.runs import RunManager
from api.runs_routes import build_router
from api.sources import Sources
from api.tamper import NothingToTamper, tamper_copy
from auditor import counterfactual as cf
from auditor.fills_csv import CsvError, parse_fills_csv
from auditor.recon import DEFAULT_TOLERANCE_S, reconcile
from auditor.replay import audit
from auditor.report import build_report
from core.rules import load_rules

ROOT = Path(__file__).resolve().parent.parent
MAX_UPLOAD = 1_000_000  # 1 MB
SourceName = Literal["demo", "recon"]


@dataclass
class Settings:
    root: Path = ROOT
    rules: Path = ROOT / "rules.yaml"
    orders: Path = ROOT / "fixtures" / "orders.jsonl"
    bench_dir: Path = ROOT / "bench" / "results"
    recon_log: Path = ROOT / "fixtures" / "recon" / "gate_log.jsonl"
    recon_csv: Path = ROOT / "fixtures" / "recon" / "broker_fills.csv"
    cf_csv: Path = ROOT / "fixtures" / "recon" / "cf_trades.csv"
    default_demo_log: Path = ROOT / "out" / "demo" / "gate.log"
    runs_dir: Path = ROOT / "runs"
    invariants_md: Path = ROOT / "docs" / "INVARIANTS.md"
    junit: Path = ROOT / "out" / "pytest-junit.xml"
    sse_poll: float = 0.1
    extra: dict = field(default_factory=dict)


class TooLarge(Exception):
    pass


async def read_capped(request: Request, cap: int = MAX_UPLOAD) -> bytes:
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > cap:
        raise TooLarge
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > cap:
            raise TooLarge
    return bytes(buf)


def _err(status: int, error: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": error, "message": message, **extra}, status_code=status)


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings()
    sources = Sources(s.root, s.recon_log, s.default_demo_log)
    runs = RunManager(s.runs_dir, s.root, s.rules, **s.extra.get("run_manager", {}))
    demo = DemoRunner(s.root, s.rules, s.orders, on_log=lambda p: setattr(sources, "demo_log", p))

    @asynccontextmanager
    async def lifespan(app):
        yield
        if demo.running:
            demo.task.cancel()
            await asyncio.gather(demo.task, return_exceptions=True)
        demo.cleanup()
        await run_in_threadpool(runs.shutdown)

    app = FastAPI(title="Trade Seatbelt API", docs_url=None, redoc_url=None, lifespan=lifespan)
    # DNS-rebinding guard: the API only answers for loopback host names.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    app.state.runs = runs
    app.include_router(build_router(runs, s.sse_poll))

    @app.get("/health")
    def health():
        return {"ok": True, "simulated": True}

    @app.get("/report")
    def report(source: SourceName = "demo"):
        rules = load_rules(s.rules)
        r = build_report(audit(sources.resolve(source), rules), source, None)  # a missing log is an empty audit
        r["source"] = source
        r["rules"] = rules.model_dump()
        return r

    @app.get("/events/stream")
    async def events_stream(request: Request, source: SourceName = "demo", tail_n: int = Query(500, alias="tail", ge=1, le=5000),
                            follow: bool = True):
        hdr = request.headers.get("last-event-id", "")
        after = int(hdr) if hdr.isdigit() else 0
        gen = tail.events(lambda: sources.resolve(source), tail=tail_n, after_seq=after, follow=follow, poll=s.sse_poll)
        return StreamingResponse(gen, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/demo/run", status_code=202)
    async def demo_run(delay_ms: int = Query(120, ge=0, le=2000)):
        try:
            return demo.start(delay_ms)
        except RuntimeError as e:
            return _err(409, "demo_running", str(e))

    @app.get("/demo/status")
    def demo_status():
        return demo.status

    @app.post("/tamper")
    def tamper(source: SourceName = "demo"):
        try:
            return tamper_copy(sources.resolve(source))
        except NothingToTamper as e:
            return _err(409, "nothing_to_tamper", str(e))

    # ---- reconciliation and counterfactual: bundled fixture on GET, your CSV on POST (raw body, <= 1 MB)
    def _csv_path(data: bytes, d: str) -> Path:
        p = Path(d) / "upload.csv"
        p.write_bytes(data)
        return p

    def _recon(csv_path: Path, label: str, lenient: bool, tol: int) -> dict:
        a = audit(s.recon_log, load_rules(s.rules))
        parsed = parse_fills_csv(csv_path, lenient=lenient)
        rc = reconcile(a.fills, parsed.fills, tol, a.rejected)
        rc["csv"] = label
        rc["rejected_rows"] = [{"row": e.row, "message": e.message} for e in parsed.errors]
        return {"input": label, "log_source": "recon", "log_events": a.events, "reconciliation": rc}

    def _cf(csv_path: Path, label: str, lenient: bool) -> dict:
        r = cf.run(csv_path, s.rules, lenient)
        r["csv"] = label
        return {"input": label, **r}

    def _guard(fn):
        try:
            return fn()
        except CsvError as e:
            return _err(422, "csv_rejected", str(e), row=e.row, detail=e.message)

    @app.get("/recon")
    def recon_fixture(lenient: bool = False, tolerance_seconds: int = Query(DEFAULT_TOLERANCE_S, ge=0, le=3600)):
        return _guard(lambda: _recon(s.recon_csv, "bundled fixture: fixtures/recon/broker_fills.csv", lenient, tolerance_seconds))

    @app.post("/recon")
    async def recon_upload(request: Request, lenient: bool = False,
                           tolerance_seconds: int = Query(DEFAULT_TOLERANCE_S, ge=0, le=3600)):
        """Body = a broker fills CSV. It is reconciled against the bundled fixture log."""
        try:
            data = await read_capped(request)
        except TooLarge:
            return _err(413, "too_large", f"upload exceeds {MAX_UPLOAD} bytes")
        with tempfile.TemporaryDirectory(prefix="ts-up-") as d:
            path = _csv_path(data, d)
            return await run_in_threadpool(lambda: _guard(lambda: _recon(path, "upload", lenient, tolerance_seconds)))

    @app.get("/counterfactual")
    def counterfactual_fixture(lenient: bool = False):
        return _guard(lambda: _cf(s.cf_csv, "bundled fixture: fixtures/recon/cf_trades.csv", lenient))

    @app.post("/counterfactual")
    async def counterfactual_upload(request: Request, lenient: bool = False):
        try:
            data = await read_capped(request)
        except TooLarge:
            return _err(413, "too_large", f"upload exceeds {MAX_UPLOAD} bytes")
        with tempfile.TemporaryDirectory(prefix="ts-up-") as d:
            path = _csv_path(data, d)
            return await run_in_threadpool(lambda: _guard(lambda: _cf(path, "upload", lenient)))

    @app.get("/bench")
    def bench():
        return bench_mod.load(s.bench_dir)

    @app.get("/invariants")
    def invariants():
        return inv_mod.build(s.invariants_md, s.junit, s.root)

    return app
