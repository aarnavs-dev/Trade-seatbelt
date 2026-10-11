"""API tests. The API reads logs; these tests also check it never writes the real one."""

import asyncio
import hashlib
import json
import runpy
import shutil
import sys
import tempfile
import time
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", message=".*httpx.*", category=DeprecationWarning)
from fastapi.testclient import TestClient  # noqa: E402

from api import tail  # noqa: E402
from api.app import MAX_UPLOAD, Settings, create_app  # noqa: E402
from core.logio import scan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures" / "recon"


@pytest.fixture
def demo_log(tmp_path):
    p = tmp_path / "gate.log"
    shutil.copy(FIX / "gate_log.jsonl", p)  # a real hash-chained log, 13 events
    return p


@pytest.fixture
def client(tmp_path, demo_log):
    with TestClient(create_app(Settings(default_demo_log=demo_log, sse_poll=0.01))) as c:
        yield c


def digest(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns, p.stat().st_size


def frames(text: str):
    out = []
    for block in text.split("\n\n"):
        f = dict(l.split(": ", 1) for l in block.splitlines() if ": " in l)
        if "event" in f:
            out.append((f["event"], json.loads(f["data"]), f.get("id")))
    return out


# ---------------------------------------------------------------- health, host guard, bind address
def test_health(client):
    assert client.get("/health").json() == {"ok": True, "simulated": True}


def test_other_host_names_are_refused(client):
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 400


def test_binds_to_loopback_only(monkeypatch):
    seen = {}
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: seen.update(k))
    monkeypatch.setattr(sys, "argv", ["api"])
    runpy.run_module("api", run_name="__main__")
    assert seen["host"] == "127.0.0.1"
    src = (ROOT / "api" / "__main__.py").read_text()
    assert 'add_argument("--host"' not in src and "0.0.0.0" not in src


# ---------------------------------------------------------------- /report
def test_report_of_a_log(client):
    r = client.get("/report").json()
    assert r["chain"] == {"ok": True, "events_verified": 13, "first_bad_seq": None, "reason": None}
    assert r["counts"]["accepted"] == 12 and r["source"] == "demo" and r["rules"]["max_position_per_symbol"] == 100


def test_report_with_no_log_is_an_empty_audit(tmp_path):
    with TestClient(create_app(Settings(default_demo_log=tmp_path / "none.log"))) as c:
        r = c.get("/report").json()
    assert r["counts"]["events"] == 0 and r["chain"]["ok"]


def test_report_unknown_source_is_rejected(client):
    assert client.get("/report?source=../../etc/passwd").status_code == 422


# ---------------------------------------------------------------- /events/stream
def test_stream_sends_every_event_in_order(client):
    f = frames(client.get("/events/stream?follow=false").text)
    assert f[0][0] == "hello" and f[0][1]["total"] == 13 and f[0][1]["first_seq"] == 1
    blocks = [x for x in f if x[0] == "block"]
    assert [b[1]["seq"] for b in blocks] == list(range(1, 14)) and [b[2] for b in blocks] == [str(i) for i in range(1, 14)]


def test_stream_tail_window_and_resume(client):
    f = frames(client.get("/events/stream?follow=false&tail=5").text)
    assert f[0][1]["first_seq"] == 9 and [b[1]["seq"] for b in f[1:]] == [9, 10, 11, 12, 13]
    f = frames(client.get("/events/stream?follow=false", headers={"last-event-id": "10"}).text)
    assert [b[1]["seq"] for b in f if b[0] == "block"] == [11, 12, 13]


def test_stream_does_not_emit_a_torn_last_line(tmp_path):
    p = tmp_path / "t.log"
    lines = (FIX / "gate_log.jsonl").read_bytes().splitlines(keepends=True)
    p.write_bytes(b"".join(lines[:3]) + lines[3][:40])
    with TestClient(create_app(Settings(default_demo_log=p))) as c:
        f = frames(c.get("/events/stream?follow=false").text)
    assert [b[1]["seq"] for b in f if b[0] == "block"] == [1, 2, 3]


def test_stream_follows_appends_and_resets_on_truncation(tmp_path):
    lines = (FIX / "gate_log.jsonl").read_bytes().splitlines(keepends=True)
    p = tmp_path / "live.log"
    p.write_bytes(b"".join(lines[:2]))

    async def go():
        got, gen = [], tail.events(lambda: p, poll=0.01)
        async for fr in gen:
            if b"event: block" in fr:
                got.append(json.loads(fr.split(b"data: ")[1])["seq"])
                if len(got) == 2:
                    with open(p, "ab") as f:
                        f.write(lines[2])
                    time.sleep(0.02)
                if len(got) == 3:
                    p.write_bytes(lines[0])  # shrink: log replaced
            if b"event: reset" in fr:
                return got, fr
        return got, None

    got, last = asyncio.run(asyncio.wait_for(go(), 5))
    assert got == [1, 2, 3] and last.startswith(b"id: \n")  # empty id clears the client's Last-Event-ID


def test_stream_resets_when_the_source_changes(tmp_path):
    a, b = tmp_path / "a.log", tmp_path / "b.log"
    a.write_bytes((FIX / "gate_log.jsonl").read_bytes())
    cur = [a]

    async def go():
        n = 0
        async for fr in tail.events(lambda: cur[0], poll=0.01):
            if b"event: block" in fr:
                n += 1
                if n == 13:
                    cur[0] = b
            if b"event: reset" in fr:
                return n
    assert asyncio.run(asyncio.wait_for(go(), 5)) == 13


# ---------------------------------------------------------------- /demo/run
def wait_done(c, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        s = c.get("/demo/status").json()
        if s["state"] != "running":
            assert s["state"] == "done", s  # show the runner's own error if it failed
            return s
        time.sleep(0.05)
    raise AssertionError("demo did not finish")


def test_demo_run_writes_a_fresh_verifying_log_in_a_temp_dir(tmp_path):
    real = tmp_path / "real.log"
    shutil.copy(FIX / "gate_log.jsonl", real)
    before = digest(real)
    tmpdir = Path(tempfile.gettempdir())
    dirs_before = set(tmpdir.glob("ts-demo-*"))
    with TestClient(create_app(Settings(default_demo_log=real))) as c:
        assert c.post("/demo/run?delay_ms=0").status_code == 202
        s = wait_done(c)
        assert s["state"] == "done" and s["sent"] == s["total"] == 26, s  # 26 orders, one a resend: 25 events
        rep = c.get("/report").json()
        assert rep["chain"]["ok"] and rep["counts"] == {"events": 25, "accepted": 10, "rejected": 15,
                                                       "rejects_by_reason": {"LOSS_STREAK_COOLDOWN": 1, "MAX_POSITION": 7, "PRICE_BAND": 7}}
        assert rep["pnl"]["realized_net"] == 9150  # same figure `make demo` reports (fixtures/expected.json)
        assert len(set(tmpdir.glob("ts-demo-*")) - dirs_before) == 1  # a fresh temp dir
        assert c.post("/demo/run?delay_ms=0").status_code == 202
        wait_done(c)
        assert len(set(tmpdir.glob("ts-demo-*")) - dirs_before) == 1  # the previous run's dir was replaced
        for _ in range(3):  # "done" must mean a new run is accepted at once (the gate has stopped)
            assert c.post("/demo/run?delay_ms=0").status_code == 202
            assert wait_done(c)["state"] == "done"
    assert digest(real) == before  # the pre-existing log is never touched
    assert set(tmpdir.glob("ts-demo-*")) == dirs_before  # shutdown removed the last one


def test_demo_run_refuses_a_second_concurrent_run(client):
    assert client.post("/demo/run?delay_ms=200").status_code == 202
    r = client.post("/demo/run?delay_ms=0")
    assert r.status_code == 409 and r.json()["error"] == "demo_running"


def test_demo_streams_events_live(client):
    client.post("/demo/run?delay_ms=0")
    wait_done(client)
    f = frames(client.get("/events/stream?follow=false").text)
    assert [b[1]["seq"] for b in f if b[0] == "block"] == list(range(1, 26))


# ---------------------------------------------------------------- /tamper
def test_tamper_reports_first_bad_seq_and_leaves_the_real_log_byte_identical(client, demo_log):
    before = digest(demo_log)
    r = client.post("/tamper")
    assert r.status_code == 200
    j = r.json()
    assert j["chain_ok"] is False and j["first_bad_seq"] == j["tampered_seq"] == 6
    assert j["message"] == f"chain broken at seq 6: {j['reason']}" and j["after"] == j["before"] + 1
    assert digest(demo_log) == before  # content, mtime and size unchanged
    assert [e.seq for e in scan(demo_log)][-1] == 13  # and it still verifies
    assert client.get("/report").json()["chain"]["ok"]


def test_tamper_ignores_any_path_from_the_client(client, demo_log, tmp_path):
    other = tmp_path / "other.log"
    other.write_bytes(demo_log.read_bytes())
    before = digest(other)
    r = client.post("/tamper", params={"path": str(other), "log": str(other), "line": 2}, json={"path": str(other)})
    assert r.status_code == 200 and r.json()["tampered_seq"] == 6
    assert digest(other) == before
    assert client.post("/tamper?source=" + str(other)).status_code == 422


def test_tamper_with_no_log_is_a_409(tmp_path):
    with TestClient(create_app(Settings(default_demo_log=tmp_path / "none.log"))) as c:
        assert c.post("/tamper").status_code == 409


def test_tamper_leaves_no_temp_files(client):
    tmp = Path(tempfile.gettempdir())
    before = set(tmp.glob("ts-tamper-*"))
    client.post("/tamper")
    assert set(tmp.glob("ts-tamper-*")) == before


# ---------------------------------------------------------------- /recon and /counterfactual
def test_recon_bundled_fixture_matches_hand_computed_counts(client):
    j = client.get("/recon").json()
    assert j["reconciliation"]["counts"] == {"AMBIGUOUS": 1, "MATCHED": 6, "MISSING_IN_BROKER": 2, "MISSING_IN_LOG": 5,
                                             "PRICE_MISMATCH": 1, "QTY_MISMATCH": 1}
    exp = json.loads((FIX / "expected_recon.json").read_text())
    assert exp  # the expected file exists and is the reference check_recon_expected.py uses


def test_recon_upload_equals_bundled_result(client):
    a = client.get("/recon").json()["reconciliation"]
    b = client.post("/recon", content=(FIX / "broker_fills.csv").read_bytes()).json()["reconciliation"]
    assert a["counts"] == b["counts"] and a["findings"] == b["findings"] and b["csv"] == "upload"


def test_counterfactual_bundled_and_upload(client):
    a = client.get("/counterfactual").json()
    assert a["accepted"] == 9 and a["blocked"] == 8 and a["pnl"]["impact"]["net"] == 5400
    b = client.post("/counterfactual", content=(FIX / "cf_trades.csv").read_bytes()).json()
    assert b["blocks"] == a["blocks"] and b["pnl"] == a["pnl"]


@pytest.mark.parametrize("path", ["/recon", "/counterfactual"])
def test_oversized_upload_is_rejected(client, path):
    big = b"ts,symbol,side,qty,price\n" + b"x" * MAX_UPLOAD
    assert client.post(path, content=big).status_code == 413
    ok = (FIX / "broker_fills.csv").read_bytes()
    assert client.post(path, content=ok).status_code == 200  # the cap is not a blanket refusal


def test_oversized_chunked_upload_without_content_length_is_rejected(client):
    def body():
        for _ in range(MAX_UPLOAD // 1000 + 5):
            yield b"y" * 1000
    r = client.post("/recon", content=body())
    assert r.status_code == 413


@pytest.mark.parametrize("path", ["/recon", "/counterfactual"])
def test_malformed_csv_returns_the_row_numbered_error(client, path):
    name = "broker_fills.csv" if path == "/recon" else "cf_trades.csv"
    lines = (FIX / name).read_text().splitlines()
    cells = lines[6].split(",")  # file row 7
    cells[cells.index(next(c for c in cells if c.isdigit() and int(c) > 1000))] = "421.00"  # float price
    lines[6] = ",".join(cells)
    r = client.post(path, content=("\n".join(lines) + "\n").encode())
    j = r.json()
    assert r.status_code == 422 and j["error"] == "csv_rejected" and j["row"] == 7
    assert j["message"].startswith("row 7:") and "decimal" in j["message"]


def test_bad_header_names_row_1_and_empty_body_is_rejected(client):
    r = client.post("/recon", content=b"when,what\n1,2\n")
    assert r.status_code == 422 and r.json()["row"] == 1
    assert client.post("/counterfactual", content=b"").status_code == 422


def test_lenient_upload_lists_every_bad_row(client):
    lines = (FIX / "cf_trades.csv").read_text().splitlines()
    lines[2] = lines[2].replace("BUY", "HODL").replace("SELL", "HODL")
    lines[4] = lines[4].replace("BUY", "HODL").replace("SELL", "HODL")
    j = client.post("/counterfactual?lenient=true", content=("\n".join(lines) + "\n").encode()).json()
    assert [e["row"] for e in j["rejected_rows"]] == [3, 5]


# ---------------------------------------------------------------- /bench
def test_bench_serves_stored_numbers_unchanged(client):
    j = client.get("/bench").json()
    files = {p.name: json.loads(p.read_text()) for p in (ROOT / "bench" / "results").glob("*.json")}
    assert {r["file"] for r in j["runs"]} == {n for n, d in files.items() if "summary" in d}
    final = next(r for r in j["runs"] if r["file"].startswith("final_"))
    src = next(d for n, d in files.items() if n.startswith("final_"))
    assert final["rows"][0]["orders_per_sec"] == src["summary"][0]["orders_per_sec"]
    assert final["backend"] == "native"
    assert next(r for r in j["runs"] if r["file"].startswith("docker_"))["backend"] == "docker"
    assert j["fsync_micro"]["F_FULLFSYNC"] == files[j["fsync_micro"]["file"]]["F_FULLFSYNC"]
    full = next(r for r in j["runs"] if r["label"] == "fsync_full")
    assert full["environment"]["fsync_call"] == "fcntl F_FULLFSYNC"


def test_bench_with_no_results_is_empty_not_invented(tmp_path):
    (tmp_path / "junk.json").write_text("not json")
    (tmp_path / "other.json").write_text('{"x": 1}')
    with TestClient(create_app(Settings(bench_dir=tmp_path))) as c:
        j = c.get("/bench").json()
    assert j["runs"] == [] and j["fsync_micro"] is None


# ---------------------------------------------------------------- /invariants
JUNIT = """<?xml version="1.0"?><testsuites><testsuite name="pytest" tests="4" failures="1" errors="0" skipped="1">
<testcase classname="tests.test_a" name="test_one"/><testcase classname="tests.test_a" name="test_two[x]"/>
<testcase classname="tests.test_a" name="test_two[y]"><failure message="boom"/></testcase>
<testcase classname="tests.test_b" name="test_skip"><skipped/></testcase></testsuite></testsuites>"""
MD = """| # | Claim | Tests | Command |
|---|---|---|---|
| 1 | First claim. More text. | `tests/test_a.py::test_one` | `x` |
| 2 | Second claim. | `tests/test_a.py::test_two` | `x` |
| 3 | Third claim. | `tests/test_a.py::test_one`; `::test_missing` | `x` |
| 4 | Fourth claim. | enforced by the harness | `x` |
| 5 | Whole file. | `tests/test_b.py` | `x` |
"""


def inv_client(tmp_path, junit=True):
    md = tmp_path / "INV.md"
    md.write_text(MD)
    j = tmp_path / "junit.xml"
    if junit:
        j.write_text(JUNIT)
    return TestClient(create_app(Settings(invariants_md=md, junit=j, root=tmp_path)))


def test_invariants_status_comes_only_from_the_result_file(tmp_path):
    with inv_client(tmp_path) as c:
        j = c.get("/invariants").json()
    st = {r["id"]: r for r in j["invariants"]}
    assert j["result_file_found"] and j["suite"] == {"tests": 4, "failures": 1, "errors": 0, "skipped": 1}
    assert st["1"]["status"] == "passing" and st["1"]["claim"] == "First claim."
    assert st["2"]["status"] == "failing" and (st["2"]["passed"], st["2"]["failed"]) == (1, 1)  # parameterized: any failure fails it
    assert st["3"]["status"] == "partial" and st["3"]["unmatched"] == 1  # a ref with no result is never counted as passing
    assert st["4"]["status"] == "not_pytest"
    assert st["5"]["status"] == "unknown" and st["5"]["skipped"] == 1  # only skipped tests: not "passing"


def test_invariants_without_a_result_file_are_unknown_not_passing(tmp_path):
    with inv_client(tmp_path, junit=False) as c:
        j = c.get("/invariants").json()
    assert not j["result_file_found"] and "make test" in j["note"]
    assert {r["status"] for r in j["invariants"]} <= {"unknown", "not_pytest"}


def test_real_invariants_file_parses_every_row(client):
    j = client.get("/invariants").json()
    assert [r["id"] for r in j["invariants"]][:3] == ["1", "2", "3"] and len(j["invariants"]) == 22
    assert all(r["refs"] for r in j["invariants"] if r["id"] != "9")  # only the benchmark-harness row has no pytest ref


def test_demo_fails_with_a_clear_message_if_the_gate_does_not_start(tmp_path, monkeypatch):
    import api.demo as demo_mod
    monkeypatch.setattr(demo_mod, "GATE_START_TIMEOUT_S", 0.0)  # the deadline expires before the gate can listen
    with TestClient(create_app(Settings(default_demo_log=tmp_path / "x.log"))) as c:
        assert c.post("/demo/run?delay_ms=0").status_code == 202
        end = time.time() + 30
        while c.get("/demo/status").json()["state"] == "running" and time.time() < end:
            time.sleep(0.05)
        s = c.get("/demo/status").json()
        assert s["state"] == "failed" and "gate did not start in time" in s["error"], s
        assert c.post("/demo/run?delay_ms=0").status_code == 202  # and a new run is accepted afterwards
