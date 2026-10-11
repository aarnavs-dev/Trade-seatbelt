"""Follow API: validation, no client paths, the stream's status mapping against a known run, explanations, and a real run."""

import time
import warnings
from pathlib import Path

import orjson
import pytest

warnings.filterwarnings("ignore", message=".*httpx.*", category=DeprecationWarning)
from fastapi.testclient import TestClient  # noqa: E402

from api.app import Settings, create_app  # noqa: E402
from copier.config import FollowerConfig  # noqa: E402
from copier.follow import run_follower, write_status  # noqa: E402
from core.rules import load_rules  # noqa: E402
from sim.runfiles import sha256_file  # noqa: E402
from tests.test_copier import BASE, ThreadGate, fixture_run, sigd  # noqa: E402


def make_client(tmp_path, pace=0):
    return TestClient(create_app(Settings(runs_dir=tmp_path / "runs", sse_poll=0.01, follow_pace_ms=pace)))


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        c.runs_dir = tmp_path / "runs"
        yield c


def known_run(tmp_path):
    """One finished run whose six outcomes are known in advance (limit: 5 shares per symbol)."""
    cfg = FollowerConfig(allowlist=("local", "Leader111"), scale_num=1, scale_den=1, leader_drawdown_stop=10**6)
    d = fixture_run(tmp_path, overrides={"max_position_per_symbol": 5}, cfg=cfg, name="runs")
    rows = [
        {"signal": sigd(1, qty=4)},                                                                 # 1 COPIED
        {"signal": sigd(2, qty=4)},                                                                 # 2 REJECTED MAX_POSITION (8 > 5)
        {"signal": sigd(3, side="SELL", qty=10)},                                                   # 3 CLIPPED to the 4 held
        {"signal": sigd(4, side="SELL", qty=3)},                                                    # 4 STOPPED SELL_WITHOUT_POSITION
        {"signal": {"junk": 1}},                                                                    # 5 STOPPED INVALID_SIGNAL
        {"signal": sigd(1, qty=4)},                                                                 # 6 STOPPED DUPLICATE_SIGNAL
        {"signal": sigd(7, symbol="MSFT", qty=2, price=46000), "via": "chain", "tx": "5abcTx", "signer": "Leader111"},   # 7 COPIED, chain
    ]
    (d / "feed.jsonl").write_bytes(b"".join(orjson.dumps(r) + b"\n" for r in rows))
    with ThreadGate(load_rules(d / "rules.yaml"), d / "gate.log") as g:
        run_follower(d, g.port)
    write_status(d, "done")
    return d


def sse(text):
    out = []
    for block in text.strip().split("\n\n"):
        ev = {}
        for ln in block.splitlines():
            if ln.startswith(":"):
                continue
            k, _, v = ln.partition(": ")
            ev[k] = v
        if ev:
            out.append(ev)
    return out


def test_stream_maps_every_signal_to_its_status(tmp_path):
    d = known_run(tmp_path)
    with make_client(tmp_path) as c:
        events = sse(c.get(f"/follow/{d.name}/stream").text)
    rows = [orjson.loads(e["data"]) for e in events if e["event"] == "signal"]
    assert [(r["n"], r["status"], r["reason_code"]) for r in rows] == [
        (1, "COPIED", "OK"), (2, "REJECTED_BY_RULES", "MAX_POSITION"), (3, "CLIPPED", "SIZE_CLIPPED"),
        (4, "STOPPED_BY_COPIER", "SELL_WITHOUT_POSITION"), (5, "STOPPED_BY_COPIER", "INVALID_SIGNAL"),
        (6, "STOPPED_BY_COPIER", "DUPLICATE_SIGNAL"), (7, "COPIED", "OK")]
    assert events[-1]["event"] == "end" and orjson.loads(events[-1]["data"])["state"] == "done"
    r3, r7 = rows[2], rows[6]
    assert (r3["qty"], r3["leader_qty"], r3["symbol"], r3["side"]) == (4, 10, "AAPL", "SELL")
    assert r7["leader"] == "Leader111" and r7["explorer_url"] == "https://explorer.solana.com/tx/5abcTx?cluster=devnet"
    assert all(r["explorer_url"] is None for r in rows[:6])                    # local-feed signals have no transaction
    assert all(r["reason_text"] for r in rows) and "nothing to sell" in rows[3]["reason_text"]
    # resuming after row 5 sends only the rest
    with make_client(tmp_path) as c:
        rest = sse(c.get(f"/follow/{d.name}/stream", headers={"last-event-id": "5"}).text)
    assert [orjson.loads(e["data"])["n"] for e in rest if e["event"] == "signal"] == [6, 7]


def test_summary_counts_and_manifest_fields(tmp_path):
    d = known_run(tmp_path)
    with make_client(tmp_path) as c:
        s = c.get(f"/follow/{d.name}/summary").json()
    assert (s["seen"], s["total_in_feed"], s["copied"], s["clipped"], s["stopped"], s["rejected"]) == (7, 7, 3, 1, 3, 1)
    assert s["stopped_by_reason"] == {"DUPLICATE_SIGNAL": 1, "INVALID_SIGNAL": 1, "SELL_WITHOUT_POSITION": 1}
    assert s["rejected_by_reason"] == {"MAX_POSITION": 1} and s["state"] == "done"
    assert s["data_label"] == "SYNTHETIC FIXTURE" and s["data"]["synthetic"] is True and s["paper_only"] is True
    assert s["feed"]["source"] == "local feed" and s["limits"]["max_position_per_symbol"] == 5


def test_explain_each_kind_and_refuse_bad_ids(tmp_path):
    d = known_run(tmp_path)
    with make_client(tmp_path) as c:
        ex = lambda n: c.get(f"/follow/{d.name}/explain/{n}")
        rej = ex(2).json()
        assert rej["rule"] == "max position per symbol" and rej["limit"] == "5 shares" and rej["value_before"] == "4 shares"
        assert rej["value_if_accepted"] == "8 shares" and rej["leader_signal"]["leader_seq"] == 2 and "protects" not in rej["detail"]
        assert rej["protects_against"]
        assert "clipped to the 4 AAPL" in ex(3).json()["detail"] and ex(3).json()["rule"] == "SIZE_CLIPPED"
        assert ex(4).json()["rule"] == "SELL_WITHOUT_POSITION" and ex(4).json()["protects_against"]
        assert ex(7).json()["explorer_url"].endswith("5abcTx?cluster=devnet") and ex(1).json()["status"] == "COPIED"
        assert ex(99).status_code == 404
        assert c.get("/follow/..%2f..%2fetc/explain/1").status_code == 404
        assert c.get("/follow/20200101-000000-abcd/summary").status_code == 404           # well-formed id, no such run
        assert c.get(f"/follow/{d.name}/explain/x").status_code == 422
        # a sim run (not a follow run) is not served here
        (d.parent / "20200101-000000-abcd").mkdir()
        (d.parent / "20200101-000000-abcd" / "manifest.json").write_text('{"kind": "sim"}')
        assert c.get("/follow/20200101-000000-abcd/summary").status_code == 404
        # a damaged gate log is refused, not explained
        raw = (d / "gate.log").read_bytes()
        (d / "gate.log").write_bytes(raw.replace(b'"qty":4', b'"qty":9', 1))
        assert ex(2).status_code == 409


def test_start_validates_limits_and_never_takes_a_path(client):
    for limits, word in (({"max_daily_loss": 0}, "positive"), ({"max_position_per_symbol": -3}, "positive"),
                         ({"price_band_pct": 1.5}, "positive integer"), ({"bogus": 1}, "unknown limit"),
                         ({"kill_switch": 1}, "unknown limit"), ({"fees": {}}, "unknown limit"),
                         ({"loss_streak_cooldown": {"losses": 0}}, "positive"), ({"max_daily_loss": True}, "positive")):
        r = client.post("/follow/run", json={"data": "fixture", "limits": limits})
        assert r.status_code == 422 and r.json()["error"] == "bad_limit" and word in r.json()["message"], (limits, r.text)
    for extra in ({"path": "/etc/passwd"}, {"rules": "x.yaml"}, {"runs_dir": "/tmp"}, {"log": "gate.log"}, {"offline": True}):
        assert client.post("/follow/run", json={"data": "fixture", **extra}).status_code == 422
    assert client.post("/follow/run", json={"feed": "../x"}).status_code == 422
    assert not client.runs_dir.exists() or not any(p.is_dir() for p in client.runs_dir.iterdir())   # nothing was started


def test_a_real_run_through_the_api_uses_the_override_and_the_label(tmp_path):
    before = sha256_file(BASE)
    with make_client(tmp_path) as c:
        r = c.post("/follow/run", json={"data": "fixture", "feed": "local", "leader": "momentum",
                                        "limits": {"max_position_per_symbol": 7, "loss_streak_cooldown": {"losses": 2}}})
        assert r.status_code == 201
        run = r.json()["run"]
        end = time.monotonic() + 60
        while c.get(f"/follow/{run}/summary").json()["state"] not in ("done", "failed", "stopped") and time.monotonic() < end:
            time.sleep(0.1)
        s = c.get(f"/follow/{run}/summary").json()
        events = sse(c.get(f"/follow/{run}/stream").text)
    assert s["state"] == "done" and s["seen"] == s["total_in_feed"] > 0
    assert s["data_label"] == "SYNTHETIC FIXTURE" and s["feed"]["badge"] == "local feed"
    assert s["limits"]["max_position_per_symbol"] == 7 and s["limits"]["cooldown_after_losses"] == 2
    assert s["limit_overrides"] == {"max_position_per_symbol": 7, "loss_streak_cooldown": {"losses": 2}}
    assert sum(1 for e in events if e["event"] == "signal") == s["seen"] and events[-1]["event"] == "end"
    assert sha256_file(BASE) == before                                             # the repo's rules.yaml is never touched


def test_only_one_run_at_a_time_and_stop_ends_it(tmp_path):
    with make_client(tmp_path, pace=150) as c:
        run = c.post("/follow/run", json={"data": "fixture"}).json()["run"]
        again = c.post("/follow/run", json={"data": "fixture"})
        assert again.status_code == 409 and again.json()["error"] == "follow_running"
        assert c.post(f"/follow/{run}/stop").json()["stopped"] is True
        end = time.monotonic() + 30
        while c.get(f"/follow/{run}/summary").json()["state"] not in ("done", "failed", "stopped") and time.monotonic() < end:
            time.sleep(0.1)
        s = c.get(f"/follow/{run}/summary").json()
    assert s["state"] == "stopped" and s["seen"] < s["total_in_feed"]
