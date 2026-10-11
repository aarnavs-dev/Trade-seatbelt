"""Runs API: real simulator and gate subprocesses (offline prices), driven through the HTTP routes."""

import hashlib
import socket
import time
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", message=".*httpx.*", category=DeprecationWarning)
from fastapi.testclient import TestClient  # noqa: E402

from api.app import Settings, create_app  # noqa: E402
from core.logio import scan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BODY = {"provider": "offline", "profile": "impulsive", "seed": 5, "speed": 100, "max_orders": 60}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(runs_dir=tmp_path / "runs", sse_poll=0.01))) as c:
        c.runs_dir = tmp_path / "runs"
        yield c


def wait_for(client, run_id, pred, timeout=25.0):
    end, info = time.monotonic() + timeout, None
    while time.monotonic() < end:
        info = client.get(f"/runs/{run_id}").json()
        if pred(info):
            return info
        time.sleep(0.05)
    raise AssertionError(f"timed out; last info: { {k: info[k] for k in ('state', 'sent', 'error')} if info else None}")


def finished(info):
    return info["state"] in ("done", "stopped", "failed", "interrupted")


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_create_run_to_completion_then_report_and_stream(client):
    r = client.post("/runs", json=BODY)
    assert r.status_code == 201
    info = r.json()
    rid = info["id"]
    assert info["simulated"] and info["seed"] == 5 and info["provider"] == "offline" and info["selected"]
    done = wait_for(client, rid, finished)
    assert done["state"] == "done" and done["sent"] == 60 and done["accepted"] + done["rejected"] == 60
    lst = client.get("/runs").json()
    assert [x["id"] for x in lst["runs"]] == [rid] and lst["selected"] == rid and lst["active"] is None
    rep = client.get(f"/runs/{rid}/report").json()
    assert rep["counts"]["events"] == 60 and rep["chain"]["ok"] and rep["findings"] == []
    assert rep["rules"]["symbols"]["AAPL"]["reference_price"] == 19000
    log = client.runs_dir / rid / "gate.log"
    assert len(list(scan(log))) == 60
    frames = client.get(f"/runs/{rid}/events/stream", params={"follow": "false"}).text
    assert frames.count("event: block") == 60 and "event: hello" in frames


def test_get_endpoints_never_modify_the_log(client):
    rid = client.post("/runs", json=BODY).json()["id"]
    wait_for(client, rid, finished)
    log = client.runs_dir / rid / "gate.log"
    before = (hashlib.sha256(log.read_bytes()).hexdigest(), log.stat().st_mtime_ns)
    for url in (f"/runs/{rid}", "/runs", f"/runs/{rid}/report"):
        client.get(url)
    client.get(f"/runs/{rid}/events/stream", params={"follow": "false"})
    assert (hashlib.sha256(log.read_bytes()).hexdigest(), log.stat().st_mtime_ns) == before


def test_pause_resume_speed_stop_and_the_gate_goes_away(client):
    rid = client.post("/runs", json={**BODY, "speed": 20, "max_orders": 5000}).json()["id"]
    live = wait_for(client, rid, lambda i: i["sent"] >= 5)
    assert live["state"] == "running"
    assert client.post("/runs", json=BODY).status_code == 409           # one active run at a time
    port = None
    assert client.post(f"/runs/{rid}/pause").json()["state"] in ("running", "paused")
    paused = wait_for(client, rid, lambda i: i["state"] == "paused")
    time.sleep(0.5)
    n = client.get(f"/runs/{rid}").json()["sent"]
    time.sleep(0.7)
    assert client.get(f"/runs/{rid}").json()["sent"] == n               # nothing is sent while paused
    st = __import__("json").loads((client.runs_dir / rid / "sim.status.json").read_text())
    port = st["port"]
    assert port_open(port)
    assert client.post(f"/runs/{rid}/speed", json={"speed": 150}).json()["speed"] == 150
    assert client.post(f"/runs/{rid}/resume").status_code == 200
    wait_for(client, rid, lambda i: i["sent"] > n + 20)
    stopped = client.post(f"/runs/{rid}/stop")
    assert stopped.status_code == 200
    fin = wait_for(client, rid, finished)
    assert fin["state"] == "stopped" and fin["sent"] < 5000
    assert not port_open(port)                                          # the run's gate was stopped too
    assert client.post(f"/runs/{rid}/pause").status_code == 409         # a finished run cannot be controlled
    assert len(list(scan(client.runs_dir / rid / "gate.log"))) == fin["sent"]
    assert client.post("/runs", json=BODY).status_code == 201           # a new run is allowed again
    wait_for(client, client.get("/runs").json()["selected"], finished)


def test_select_picks_which_run_is_shown(client):
    a = client.post("/runs", json={**BODY, "max_orders": 10}).json()["id"]
    wait_for(client, a, finished)
    time.sleep(1.1)   # run ids have one-second resolution plus 16 random bits; keep ordering unambiguous
    b = client.post("/runs", json={**BODY, "max_orders": 10}).json()["id"]
    wait_for(client, b, finished)
    assert client.get("/runs").json()["selected"] == b
    assert client.post(f"/runs/{a}/select").json()["selected"] is True
    lst = client.get("/runs").json()
    assert lst["selected"] == a and [x["selected"] for x in lst["runs"]].count(True) == 1


@pytest.mark.parametrize("extra", [{"path": "/etc/passwd"}, {"log": "x.log"}, {"runs_dir": "/tmp"}, {"rules": "rules.yaml"}])
def test_run_creation_never_accepts_a_path_from_the_client(client, extra):
    assert client.post("/runs", json={**BODY, **extra}).status_code == 422
    assert not client.runs_dir.exists() or list(client.runs_dir.iterdir()) == []


@pytest.mark.parametrize("body", [{"profile": "x"}, {"speed": 201}, {"speed": 0}, {"seed": -1}, {"max_orders": 0},
                                  {"provider": "../x"}, {"symbols": ["AAPL; rm"]}, {"symbols": ["ZZZZ"]}])
def test_bad_settings_are_rejected(client, body):
    assert client.post("/runs", json={**BODY, **body}).status_code in (422,)


@pytest.mark.parametrize("rid", ["%2e%2e", "20260101-000000-zzzz", "20990101-000000-abcd", "etc%2fpasswd", "..%2f..%2fetc"])
def test_unknown_or_malicious_run_ids_are_404(client, rid):
    for method, suffix in (("get", ""), ("post", "/select"), ("post", "/stop"), ("get", "/report"), ("get", "/events/stream")):
        assert getattr(client, method)(f"/runs/{rid}{suffix}").status_code in (404, 405), (method, suffix)


def test_speed_endpoint_rejects_out_of_range_and_extra_fields(client):
    rid = client.post("/runs", json={**BODY, "speed": 5, "max_orders": 3000}).json()["id"]
    wait_for(client, rid, lambda i: i["sent"] >= 1)
    assert client.post(f"/runs/{rid}/speed", json={"speed": 1000}).status_code == 422
    assert client.post(f"/runs/{rid}/speed", json={"speed": 5, "path": "x"}).status_code == 422
    client.post(f"/runs/{rid}/stop")
    wait_for(client, rid, finished)


def test_leaving_the_api_stops_the_simulator_and_its_gate(tmp_path):
    with TestClient(create_app(Settings(runs_dir=tmp_path / "runs", sse_poll=0.01))) as c:
        rid = c.post("/runs", json={**BODY, "speed": 20, "max_orders": 5000}).json()["id"]
        wait_for(c, rid, lambda i: i["sent"] >= 3)
        manager = c.app.state.runs
        proc = manager.procs[rid]
    assert proc.poll() is not None
    import json
    st = json.loads((tmp_path / "runs" / rid / "sim.status.json").read_text())
    assert st["state"] == "stopped" and not port_open(st["port"])


def test_a_killed_simulator_is_shown_as_interrupted_and_its_orphan_gate_is_stopped(client):
    import json
    rid = client.post("/runs", json={**BODY, "speed": 20, "max_orders": 5000}).json()["id"]
    wait_for(client, rid, lambda i: i["sent"] >= 3)
    port = json.loads((client.runs_dir / rid / "sim.status.json").read_text())["port"]
    client.app.state.runs.procs[rid].kill()                    # kill -9: no chance to stop its gate
    assert wait_for(client, rid, lambda i: i["state"] == "interrupted")["state"] == "interrupted"
    end = time.monotonic() + 10
    while port_open(port) and time.monotonic() < end:
        time.sleep(0.1)
    assert not port_open(port)
    assert client.post("/runs", json={**BODY, "max_orders": 5}).status_code == 201   # interrupted does not block new runs
    wait_for(client, client.get("/runs").json()["selected"], finished)
