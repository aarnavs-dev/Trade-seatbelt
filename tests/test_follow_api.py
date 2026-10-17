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


def make_client(tmp_path, pace="off"):
    return TestClient(create_app(Settings(runs_dir=tmp_path / "runs", sse_poll=0.01, follow_pace_override=pace,
                                          profile=tmp_path / "profile" / "rules.json")))


BALANCED = {"max_position_per_symbol": 7, "max_daily_loss": 5000, "price_band_pct": 5, "loss_streak_cooldown": {"losses": 2, "cooldown_seconds": 300}}


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
    assert rows[1]["reason_text"] == "AAPL would go from 4 to 8 shares; the limit is 5."          # plain English, same as the explanation
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


def test_rules_validation_save_hash_and_the_409_rule(client, tmp_path):
    g = client.get("/rules").json()
    assert g["saved"] is False and g["hash"] is None and set(g["presets"]) == {"cautious", "balanced", "active"}
    assert g["defaults"]["max_position_per_symbol"] == 100
    # no run without saved rules
    r = client.post("/follow/run", json={"data": "fixture"})
    assert r.status_code == 409 and r.json()["error"] == "rules_not_saved" and "Save your rules" in r.json()["message"]
    bad = [({**BALANCED, "max_daily_loss": 0}, "positive"), ({**BALANCED, "max_position_per_symbol": -3}, "positive"),
           ({**BALANCED, "price_band_pct": 1.5}, "positive integer"), ({**BALANCED, "bogus": 1}, "unknown limit"),
           ({**BALANCED, "kill_switch": 1}, "unknown limit"), ({**BALANCED, "fees": {}}, "unknown limit"),
           ({**BALANCED, "max_daily_loss": True}, "positive"), ({"max_daily_loss": 5000}, "missing"),
           ({**BALANCED, "loss_streak_cooldown": {"losses": 0, "cooldown_seconds": 5}}, "positive"),
           ({**BALANCED, "loss_streak_cooldown": {"losses": 2}}, "both"), ({**BALANCED, "price_band_pct": 101}, "no larger than 100")]
    for body, word in bad:
        r = client.put("/rules", json=body)
        assert r.status_code == 422 and r.json()["error"] == "bad_limit" and word in r.json()["message"], (body, r.text)
    assert client.put("/rules", content=b"not json").status_code == 422
    assert client.get("/rules").json()["saved"] is False and not (tmp_path / "profile").exists()      # nothing was written
    before = sha256_file(BASE)
    ok = client.put("/rules", json=BALANCED).json()
    assert ok["saved"] and len(ok["hash"]) == 64 and ok["saved_at"].endswith("+00:00") and ok["limits"] == BALANCED
    assert client.get("/rules").json()["hash"] == ok["hash"] and sha256_file(BASE) == before          # the repo's rules.yaml is untouched
    assert (tmp_path / "profile" / "rules.json").exists()
    assert client.put("/rules", json={**BALANCED, "max_position_per_symbol": 8}).json()["hash"] != ok["hash"]
    # a damaged profile file counts as not saved
    (tmp_path / "profile" / "rules.json").write_text('{"limits": {}, "hash": "x"}')
    assert client.get("/rules").json()["saved"] is False


def test_profile_file_is_gitignored():
    assert "profile/" in (Path(__file__).resolve().parent.parent / ".gitignore").read_text().split()


def test_start_validates_options_and_never_takes_a_path(client):
    client.put("/rules", json=BALANCED)
    for size, ses, word in (({"mode": "percent"}, {}, "give a value"), ({"mode": "percent", "value": 0}, {}, "between 1 and 1000"),
                            ({"mode": "fixed"}, {}, "give a value"), ({}, {"max_trades": 0}, "positive"),
                            ({}, {"max_volume": -5}, "positive"), ({}, {"max_total_amount": 0}, "positive")):
        r = client.post("/follow/run", json={"data": "fixture", "size": size, "session": ses})
        assert r.status_code == 422 and r.json()["error"] == "bad_option" and word in r.json()["message"], (size, ses, r.text)
    for extra in ({"path": "/etc/passwd"}, {"rules": "x.yaml"}, {"runs_dir": "/tmp"}, {"log": "gate.log"}, {"offline": True},
                  {"limits": {"max_daily_loss": 1}}, {"session": {"file": "x"}}, {"size": {"mode": "same", "path": "x"}}):
        assert client.post("/follow/run", json={"data": "fixture", **extra}).status_code == 422, extra
    for bad in ({"feed": "../x"}, {"pace": "ludicrous"}, {"size": {"mode": "half"}}, {"session": {"max_trades": 1.5}}):
        assert client.post("/follow/run", json=bad).status_code == 422
    assert not client.runs_dir.exists() or not any(p.is_dir() for p in client.runs_dir.iterdir())   # nothing was started


def test_leaders_list_has_a_public_key_only(client):
    j = client.get("/follow/leaders").json()
    assert [x["id"] for x in j["leaders"]] == ["momentum", "orb", "meanrev"] and all(x["about"] for x in j["leaders"])
    for x in j["leaders"]:
        assert x["public_key"] is None or x["explorer_url"] == f"https://explorer.solana.com/address/{x['public_key']}?cluster=devnet"
    assert "secret" not in client.get("/follow/leaders").text.lower().replace("the secret is", "")


def test_a_real_run_through_the_api_uses_the_override_and_the_label(tmp_path):
    before = sha256_file(BASE)
    with make_client(tmp_path) as c:
        c.put("/rules", json=BALANCED)
        r = c.post("/follow/run", json={"data": "fixture", "feed": "local", "leader": "momentum", "pace": "fast",
                                        "size": {"mode": "percent", "value": 150}, "session": {"max_trades": 3}})
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
    assert s["limit_overrides"]["max_position_per_symbol"] == 7 and s["pace"]["name"] == "off"        # the test override wins
    assert s["copy"]["scale"] == "150/100" and s["copy"]["session_limits"]["max_trades"] == 3
    assert sum(1 for e in events if e["event"] == "signal") == s["seen"] and events[-1]["event"] == "end"
    assert sha256_file(BASE) == before                                             # the repo's rules.yaml is never touched


def test_only_one_run_at_a_time_and_stop_ends_it(tmp_path):
    with make_client(tmp_path, pace=None) as c:             # the real pace (seconds between signals) so the run is still going
        c.put("/rules", json=BALANCED)
        run = c.post("/follow/run", json={"data": "fixture", "feed": "local", "pace": "relaxed"}).json()["run"]
        again = c.post("/follow/run", json={"data": "fixture"})
        assert again.status_code == 409 and again.json()["error"] == "follow_running"
        assert c.post(f"/follow/{run}/stop").json()["stopped"] is True
        end = time.monotonic() + 30
        while c.get(f"/follow/{run}/summary").json()["state"] not in ("done", "failed", "stopped") and time.monotonic() < end:
            time.sleep(0.1)
        s = c.get(f"/follow/{run}/summary").json()
    assert s["state"] == "stopped" and s["seen"] < s["total_in_feed"]


# ---------------------------------------------------------------- where the signals came from (signal_source, row source)
def with_feed(tmp_path, feed):
    """known_run (6 local-feed rows + 1 row carrying a transaction) with the manifest's feed block replaced."""
    d = known_run(tmp_path)
    m = orjson.loads((d / "manifest.json").read_bytes())
    m["feed"] = feed
    (d / "manifest.json").write_bytes(orjson.dumps(m))
    return d


def summary_and_rows(tmp_path, d):
    with make_client(tmp_path) as c:
        s = c.get(f"/follow/{d.name}/summary").json()
        rows = [orjson.loads(e["data"]) for e in sse(c.get(f"/follow/{d.name}/stream").text) if e["event"] == "signal"]
    return s, rows


def test_a_local_feed_run_is_labelled_as_not_from_solana(tmp_path):
    s, rows = summary_and_rows(tmp_path, known_run(tmp_path))
    ss = s["signal_source"]
    assert ss["kind"] == "local" and "NOT from Solana" in ss["label"] and ss["live_chain_read"] is False
    assert ss["fallback"] is False and ss["fallback_why"] is None and ss["publisher_key"] is None
    assert ss["leader"]["id"] == "momentum" and ss["leader"]["name"] == "Momentum" and ss["leader"]["strategy"]
    assert ss["data"]["synthetic"] is True and ss["data"]["label"] == "SYNTHETIC FIXTURE"
    assert [r["source"] for r in rows] == ["local"] * 6 + ["chain"]       # a row is chain only if it carries a transaction


def test_a_live_chain_run_names_the_publisher_and_the_first_transaction(tmp_path):
    feed = {"source": "chain", "badge": "chain (devnet)", "requested": "chain", "leader": "Leader111", "rpc_host": "api.devnet.solana.com"}
    s, rows = summary_and_rows(tmp_path, with_feed(tmp_path, feed))
    ss = s["signal_source"]
    assert ss["kind"] == "chain" and ss["label"] == "Solana devnet" and ss["live_chain_read"] is True and ss["fallback"] is False
    assert ss["publisher_key"] == "Leader111" and ss["publisher_explorer_url"] == "https://explorer.solana.com/address/Leader111?cluster=devnet"
    assert (ss["first_tx"], ss["with_transaction"], ss["signals"]) == ("5abcTx", 1, 7)
    assert ss["first_tx_explorer_url"] == "https://explorer.solana.com/tx/5abcTx?cluster=devnet" and ss["rpc_host"] == "api.devnet.solana.com"
    # even in a chain run, a signal with no transaction is never shown as chain data
    assert [r["source"] for r in rows if not r["tx"]] == ["local"] * 6 and rows[6]["source"] == "chain"


def test_a_saved_copy_is_not_called_a_live_chain_read_and_counts_as_a_fallback(tmp_path):
    feed = {"source": "chain cache (stale)", "badge": "chain (devnet, cached)", "requested": "chain", "leader": "Leader111",
            "why": "devnet returned HTTP 429"}
    s, rows = summary_and_rows(tmp_path, with_feed(tmp_path, feed))
    ss = s["signal_source"]
    assert ss["kind"] == "chain_cache" and "saved copy" in ss["label"] and ss["live_chain_read"] is False
    assert ss["fallback"] is True and ss["fallback_why"] == "devnet returned HTTP 429" and ss["publisher_key"] == "Leader111"
    assert rows[6]["source"] == "chain_cache"


def test_falling_back_from_chain_to_the_local_feed_is_stated_with_its_reason(tmp_path):
    feed = {"source": "local feed", "badge": "local feed", "requested": "chain", "why": "the leader has published no signals on devnet yet"}
    s, _ = summary_and_rows(tmp_path, with_feed(tmp_path, feed))
    ss = s["signal_source"]
    assert ss["kind"] == "local" and ss["fallback"] is True and "no signals on devnet" in ss["fallback_why"] and ss["publisher_key"] is None


def test_asking_for_a_local_feed_is_not_a_fallback(tmp_path):
    s, _ = summary_and_rows(tmp_path, with_feed(tmp_path, {"source": "local feed", "badge": "local feed"}))
    assert s["signal_source"]["fallback"] is False


@pytest.mark.parametrize("feed", [{}, {"source": "something new"}, {"badge": "chain (devnet)"}])
def test_a_missing_or_unknown_feed_source_never_reads_as_chain(tmp_path, feed):
    s, _ = summary_and_rows(tmp_path, with_feed(tmp_path, feed))
    assert s["signal_source"]["kind"] == "local" and s["signal_source"]["live_chain_read"] is False


def test_skipped_before_the_gate_and_blocked_by_your_rules_are_the_two_existing_counts(tmp_path):
    s, _ = summary_and_rows(tmp_path, known_run(tmp_path))
    assert (s["skipped_before_gate"], s["blocked_by_rules"]) == (s["stopped"], s["rejected"]) == (3, 1)
    assert s["skipped_before_gate_by_reason"] == {"DUPLICATE_SIGNAL": 1, "INVALID_SIGNAL": 1, "SELL_WITHOUT_POSITION": 1}
    assert s["blocked_by_rules_by_reason"] == {"MAX_POSITION": 1}
    # every signal is exactly one of: copied (clipped ones included), skipped before the gate, blocked by the rules
    assert s["copied"] + s["skipped_before_gate"] + s["blocked_by_rules"] == s["seen"]
