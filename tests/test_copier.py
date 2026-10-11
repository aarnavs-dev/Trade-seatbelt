"""Copier + explanation tests. Gate-bound tests run the real Gate/server in a thread with a counter clock (so logs are
byte-reproducible) and the copier as a normal TCP client."""

import asyncio
import hashlib
import itertools
import os
import signal as sig_mod
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import orjson
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from auditor.explain import explain_copier_log, explain_events, explain_log
from copier import data as datamod
from copier import ledger
from copier.client import GateClient
from copier.config import ConfigError, FollowerConfig, parse_override_args, validate_overrides
from copier.copier import Copier, order_id_for, scale_qty, tradable
from copier.feed import Incoming, read_local
from copier.follow import run_follower
from copier.run import build_rules, create_follow_run
from core.events import Order
from core.ledger import Ledger
from core.logio import scan
from core.rules import Rules, load_rules
from gate.engine import Gate
from gate.server import start_server
from leaders.publisher import Publisher
from leaders.runner import generate
from marketdata.calendar import session_ms
from sim.runfiles import sha256_file
from tests.helpers import counter_clock, line, run, run_gate

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "rules.yaml"
DAY = date(2026, 9, 30)          # the fixture's replayed session (a Tuesday)
OPEN_MS, CLOSE_MS = session_ms(DAY)


class ThreadGate:
    """The real Gate + TCP server on its own event loop in a thread, with a deterministic clock."""

    def __init__(self, rules, log):
        self.rules, self.log = rules, log
        self.ready, self.stop_ev = threading.Event(), None
        self.t = threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True)

    async def _main(self):
        self.gate = Gate(self.rules, self.log, clock=counter_clock())
        await self.gate.start()
        server = await start_server(self.gate, "127.0.0.1", 0)
        self.port = server.sockets[0].getsockname()[1]
        self.stop_ev = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.ready.set()
        await self.stop_ev.wait()
        server.close()
        await self.gate.stop()

    def __enter__(self):
        self.t.start()
        assert self.ready.wait(10)
        return self

    def __exit__(self, *a):
        self.loop.call_soon_threadsafe(self.stop_ev.set)
        self.t.join(10)


def sigd(seq, symbol="AAPL", side="BUY", qty=10, price=20000, ts=None):
    ts = OPEN_MS + 600_000 * seq if ts is None else ts
    return dict(v=1, leader_seq=seq, symbol=symbol, side=side, qty=qty, ref_price=price, data_ts=ts, data_source="synthetic", leader_ts=ts)


def inc(seq, signer="local", **kw):
    return Incoming(signer, sigd(seq, **kw), f"line {seq}")


class Fake:
    """Stands in for the gate when only the pre-filters are under test."""

    def __init__(self):
        self.orders = []

    def __call__(self, order):
        self.orders.append(order)
        return {"decision": "ACCEPT", "reason_code": "OK", "duplicate": False}


class Skips:
    def __init__(self):
        self.lines = []

    def append(self, sid, code, text):
        self.lines.append((sid, code))


def fixture_run(tmp_path, overrides=None, cfg=None, leader="momentum", name="runs"):
    data = datamod.load_fixture()
    d = create_follow_run(tmp_path / name, base_rules=BASE, data=data, cfg=cfg or FollowerConfig(scale_num=1, scale_den=1,
                          leader_drawdown_stop=10**6), overrides=overrides, leader=leader, root=ROOT)
    pub = Publisher(d)
    for s in generate(data.bars, leader, data.provider, qty=10):
        pub.publish(s)
    return d


def follow(d):
    with ThreadGate(load_rules(d / "rules.yaml"), d / "gate.log") as g:
        return run_follower(d, g.port)


def decisions(path):
    return [(e.client_order_id, e.decision, e.reason_code) for e in scan(path)]


# ------------------------------------------------------------------ pre-filters, order and codes
def run_filters(items, cfg=None, symbols=("AAPL", "MSFT")):
    fake, sk = Fake(), Skips()
    cp = Copier(cfg or FollowerConfig(scale_num=1, scale_den=1), symbols, fake, sk)
    for i in items:
        cp.process(i)
    return cp, fake, sk.lines


def test_each_prefilter_has_its_code_and_nothing_reaches_the_gate():
    cfg = FollowerConfig(scale_num=1, scale_den=1)
    items = [
        inc(1, ts=OPEN_MS - 3_600_000),                      # MARKET_CLOSED (an hour before the open)
        Incoming("local", {**sigd(2), "qty": 1.5}, "line 2"),  # INVALID_SIGNAL
        inc(3, signer="mallory"),                            # UNTRUSTED_LEADER
        inc(4, symbol="NVDA"),                               # UNKNOWN_SYMBOL (follower trades AAPL, MSFT only)
        inc(1),                                              # DUPLICATE_SIGNAL (local:1 again)
        inc(5),                                              # copied
        inc(7),                                              # FEED_GAP (6 never arrived)
    ]
    cp, fake, skips = run_filters(items, cfg)
    assert [c for _, c in skips] == ["MARKET_CLOSED", "INVALID_SIGNAL", "UNTRUSTED_LEADER", "UNKNOWN_SYMBOL", "DUPLICATE_SIGNAL", "FEED_GAP"]
    assert [o["client_order_id"] for o in fake.orders] == [order_id_for("local:5")]
    assert cp.summary()["seen"] == 7 and cp.summary()["sent"] == 1


def test_filter_order_is_fixed():
    # invalid beats untrusted; untrusted beats duplicate; duplicate beats unknown symbol; unknown beats closed
    cp, fake, skips = run_filters([Incoming("mallory", {"junk": 1}, "line 1")])
    assert skips[0][1] == "INVALID_SIGNAL"
    cp, fake, skips = run_filters([inc(1), inc(1, signer="mallory")])
    assert [c for _, c in skips] == ["UNTRUSTED_LEADER"]
    cp, fake, skips = run_filters([inc(1), inc(1, symbol="NVDA", ts=OPEN_MS - 10**9)])
    assert [c for _, c in skips] == ["DUPLICATE_SIGNAL"]
    cp, fake, skips = run_filters([inc(1, symbol="NVDA", ts=OPEN_MS - 10**9)])
    assert [c for _, c in skips] == ["UNKNOWN_SYMBOL"]
    cp, fake, skips = run_filters([inc(1, ts=OPEN_MS - 10**9)])
    assert [c for _, c in skips] == ["MARKET_CLOSED"]


def test_stale_uses_the_replay_clock_and_delay():
    cfg = FollowerConfig(scale_num=1, scale_den=1, max_age_ms=1000)
    _, fake, skips = run_filters([inc(1)], cfg)
    assert not skips and len(fake.orders) == 1                       # age 0
    cfg = FollowerConfig(scale_num=1, scale_den=1, max_age_ms=1000, delay_ms=1001)
    _, fake, skips = run_filters([inc(1)], cfg)
    assert [c for _, c in skips] == ["STALE_SIGNAL"] and not fake.orders
    # out of order leader_ts: the second signal is older than the clock the first one set
    cfg = FollowerConfig(scale_num=1, scale_den=1, max_age_ms=1000)
    _, fake, skips = run_filters([inc(1, ts=OPEN_MS + 3_600_000), inc(2, ts=OPEN_MS + 3_000_000)], cfg)
    assert [c for _, c in skips] == ["STALE_SIGNAL"]


def test_feed_gap_and_drawdown_stop():
    _, fake, skips = run_filters([inc(1), inc(3)])
    assert [c for _, c in skips] == ["FEED_GAP"]
    # The leader loses 1000 cents on AAPL; the limit is 500. New risk is stopped, closing the follower's MSFT long is not.
    cfg = FollowerConfig(scale_num=1, scale_den=1, leader_drawdown_stop=500)
    items = [inc(1, symbol="MSFT"), inc(2, price=20000), inc(3, side="SELL", price=19900),
             inc(4, price=19900), inc(5, symbol="MSFT", side="SELL")]
    _, fake, skips = run_filters(items, cfg)
    assert skips == [("local:4", "LEADER_DRAWDOWN_STOP")]
    assert [(o["symbol"], o["side"]) for o in fake.orders] == [("MSFT", "BUY"), ("AAPL", "BUY"), ("AAPL", "SELL"), ("MSFT", "SELL")]


def test_scaling_is_integer_floor_and_zero_is_reported():
    assert [scale_qty(q, 1, 2) for q in (1, 2, 3, 9, 10)] == [0, 1, 1, 4, 5]
    assert scale_qty(7, 2, 3) == 4 and scale_qty(10, 3, 1) == 30
    cfg = FollowerConfig(scale_num=1, scale_den=2)
    _, fake, skips = run_filters([inc(1, qty=1), inc(2, qty=5)], cfg)
    assert [c for _, c in skips] == ["SIZE_ROUNDS_TO_ZERO"] and fake.orders[0]["qty"] == 2


def test_market_closed_uses_the_calendar():
    ms = lambda y, m, d, hh, mm, ss=0: int(datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc).timestamp() * 1000)
    assert tradable(ms(2026, 9, 29, 14, 0))                       # Tue 10:00 EDT
    assert not tradable(ms(2026, 9, 29, 13, 29, 59)) and tradable(ms(2026, 9, 29, 13, 30))   # 9:30 EDT open
    assert tradable(ms(2026, 9, 29, 20, 0)) and not tradable(ms(2026, 9, 29, 20, 0, 1))      # 16:00 EDT, inclusive
    assert not tradable(ms(2026, 9, 26, 15, 0))                   # Saturday
    assert not tradable(ms(2026, 9, 7, 15, 0))                    # Labor Day
    assert tradable(ms(2026, 11, 27, 17, 0)) and not tradable(ms(2026, 11, 27, 19, 0))       # early close 13:00 EST
    assert tradable(ms(2026, 3, 9, 13, 30)) and not tradable(ms(2026, 3, 9, 13, 29))         # EDT began Mar 8
    assert tradable(ms(2026, 3, 6, 14, 30)) and not tradable(ms(2026, 3, 6, 14, 29))         # still EST
    assert not tradable(ms(2030, 1, 2, 15, 0))                    # outside the calendar: closed, not guessed
    _, fake, skips = run_filters([inc(1, ts=ms(2026, 9, 26, 15, 0))])
    assert [c for _, c in skips] == ["MARKET_CLOSED"] and not fake.orders


# ------------------------------------------------------------------ close-only (no short)
def test_close_only_clips_a_sell_to_the_long_position_and_skips_when_flat():
    cfg = FollowerConfig(scale_num=1, scale_den=1)
    sk = Skips()
    sk.append = lambda sid, code, text: sk.lines.append((sid, code, text))
    fake = Fake()
    cp = Copier(cfg, ("AAPL",), fake, sk)
    for i in (inc(1, qty=4), inc(2, side="SELL", qty=10), inc(3, side="SELL", qty=3)):
        cp.process(i)
    assert [(o["side"], o["qty"]) for o in fake.orders] == [("BUY", 4), ("SELL", 4)]          # 10 clipped to the 4 held
    assert sk.lines[0][:2] == ("local:2", "SIZE_CLIPPED") and "clipped to the 4 AAPL" in sk.lines[0][2]
    assert sk.lines[1][:2] == ("local:3", "SELL_WITHOUT_POSITION") and "nothing to sell" in sk.lines[1][2]
    assert cp.summary()["clipped"] == 1 and cp.summary()["skipped"] == {"SELL_WITHOUT_POSITION": 1}
    # with the flag on, the SELL goes through unclipped
    fake = Fake()
    Copier(FollowerConfig(scale_num=1, scale_den=1, allow_short=True), ("AAPL",), fake, Skips()).process(inc(1, side="SELL", qty=7))
    assert fake.orders[0]["qty"] == 7


def test_a_rejected_buy_followed_by_the_leaders_sell_never_creates_a_short(tmp_path):
    d = fixture_run(tmp_path, overrides={"max_position_per_symbol": 5})          # the leader's BUY 10 is rejected
    rows = [sigd(1, qty=10), sigd(2, side="SELL", qty=10, price=20900), sigd(3, qty=4), sigd(4, side="SELL", qty=10, price=20900)]
    (d / "feed.jsonl").write_bytes(b"".join(orjson.dumps({"signal": r}) + b"\n" for r in rows))
    s = follow(d)
    ev = list(scan(d / "gate.log"))
    assert [(e.client_order_id, e.decision) for e in ev][0] == (order_id_for("local:1"), "REJECT")
    assert [(e.side, e.qty, e.decision) for e in ev] == [("BUY", 10, "REJECT"), ("BUY", 4, "ACCEPT"), ("SELL", 4, "ACCEPT")]
    led = Ledger()
    for e in ev:
        if e.decision == "ACCEPT":
            led.fill(e.symbol, e.side, e.qty, e.price, 0)
            assert led.position("AAPL") >= 0
    lines = ledger.read(d / "copier.log")
    assert [l["reason_code"] for l in lines] == ["SELL_WITHOUT_POSITION", "SIZE_CLIPPED"]
    assert s["clipped"] == 1
    kinds = [e["kind"] for e in explain_copier_log(d / "copier.log")]
    assert kinds == ["copier_skip", "copier_clip"]


# ------------------------------------------------------------------ run setup
def test_overrides_are_validated():
    assert validate_overrides({"max_position_per_symbol": 5, "loss_streak_cooldown": {"losses": 2}})
    for bad in ({"kill_switch": 1}, {"fees": {}}, {"max_position_per_symbol": 0}, {"max_daily_loss": -1},
                {"price_band_pct": 101}, {"max_orders_per_minute": 1.5}, {"max_orders_per_minute": True},
                {"loss_streak_cooldown": {"bogus": 1}}, {"loss_streak_cooldown": {"losses": 0}}, {"loss_streak_cooldown": {}}):
        with pytest.raises(ConfigError):
            validate_overrides(bad)
    with pytest.raises(ConfigError, match="unknown limit 'nope'"):
        parse_override_args(["nope=3"])
    assert parse_override_args(["loss_streak_cooldown.losses=2", "max_daily_loss=900"]) == \
        {"loss_streak_cooldown": {"losses": 2}, "max_daily_loss": 900}


def test_run_dir_has_its_own_hashed_rules_and_previous_close_anchors(tmp_path):
    before = sha256_file(BASE)
    d = fixture_run(tmp_path, overrides={"max_position_per_symbol": 7})
    m = orjson.loads((d / "manifest.json").read_bytes())
    assert sha256_file(BASE) == before                                  # repo rules.yaml untouched
    assert m["rules_sha256"] == sha256_file(d / "rules.yaml") and m["base_rules_sha256"] == before
    r = load_rules(d / "rules.yaml")
    base = load_rules(BASE)
    assert r.max_position_per_symbol == 7 and r.max_daily_loss == base.max_daily_loss and r.fees == base.fees
    data = datamod.load_fixture()
    assert {s: c.reference_price for s, c in r.symbols.items()} == data.anchors
    assert m["data_label"] == "SYNTHETIC FIXTURE" and m["data"]["synthetic"] is True and m["paper_only"] is True
    prev = [b for b in orjson.loads(datamod.FIXTURE.read_bytes())["bars"] if b[0] == "AAPL" and b[1] <= OPEN_MS]
    assert data.anchors["AAPL"] == max(prev, key=lambda b: b[1])[5]      # previous session's last-bar close


def test_failed_fetch_falls_back_to_the_labelled_fixture(tmp_path):
    class Boom:
        name = "boom"

        def fetch(self, *a):
            raise OSError("network down")
    d = datamod.load(provider=Boom(), cache_dir=tmp_path / "c", today=date(2026, 10, 12))
    assert d.synthetic and d.label == "SYNTHETIC FIXTURE" and "network down" in d.fallback_reason


# ------------------------------------------------------------------ gate-bound behaviour
def test_untrusted_and_malformed_signals_never_reach_the_gate(tmp_path):
    d = fixture_run(tmp_path)
    good = [inc(1), inc(2, symbol="MSFT", price=45000)]
    junk = [Incoming("mallory", sigd(10), "tx a"), Incoming("local", {**sigd(11), "qty": 2.5}, "line"),
            Incoming("local", {**sigd(12), "extra": 1}, "line"), Incoming("local", "not an object", "line"),
            Incoming("local", {**sigd(13), "ref_price": True}, "line"), Incoming("mallory", {"x": 1}, "tx b")]
    with ThreadGate(load_rules(d / "rules.yaml"), d / "gate.log") as g:
        c = GateClient(g.port)
        lg = ledger.CopierLog(d / "copier.log")
        cp = Copier(FollowerConfig(scale_num=1, scale_den=1), load_rules(d / "rules.yaml").symbols, c.submit, lg)
        for i in [good[0], *junk, good[1]]:
            cp.process(i)
        c.close()
    ids = {e.client_order_id for e in scan(d / "gate.log")}
    assert ids == {order_id_for("local:1"), order_id_for("local:2")}
    assert [l["reason_code"] for l in ledger.read(d / "copier.log")] == \
        ["UNTRUSTED_LEADER", "INVALID_SIGNAL", "INVALID_SIGNAL", "INVALID_SIGNAL", "INVALID_SIGNAL", "INVALID_SIGNAL"]


def test_replay_and_restart_never_execute_a_signal_twice(tmp_path):
    d = fixture_run(tmp_path)
    lines = (d / "feed.jsonl").read_bytes().splitlines()
    (d / "feed.jsonl").write_bytes(b"\n".join(lines + lines) + b"\n")      # every signal delivered twice
    with ThreadGate(load_rules(d / "rules.yaml"), d / "gate.log") as g:
        s1 = run_follower(d, g.port)
        log1, led1 = (d / "gate.log").read_bytes(), (d / "copier.log").read_bytes()
        s2 = run_follower(d, g.port)                                       # a restart over the same feed and gate
        assert s2["resent"] == s2["sent"] > 0 and s1["resent"] == 0
    assert (d / "gate.log").read_bytes() == log1 and (d / "copier.log").read_bytes() == led1
    ids = [e.client_order_id for e in scan(d / "gate.log")]
    assert len(ids) == len(set(ids)) == s1["sent"]
    assert s1["skipped"].get("DUPLICATE_SIGNAL") == len(lines)


def test_a_killed_and_restarted_copier_matches_a_clean_run(tmp_path):
    def make(name):
        d = fixture_run(tmp_path, name=name)
        rows = [sigd(n, symbol=("AAPL", "MSFT")[n % 2], side=("BUY", "SELL")[(n // 2) % 2], qty=3 + n % 4,
                     price=(20800 if n % 2 == 0 else 46500) + n) for n in range(1, 61)]
        (d / "feed.jsonl").write_bytes(b"".join(orjson.dumps({"signal": r, "via": "local-only", "tx": None}) + b"\n" for r in rows))
        return d
    clean, killed = make("a"), make("b")
    with ThreadGate(load_rules(clean / "rules.yaml"), clean / "gate.log") as g:
        run_follower(clean, g.port)
    with ThreadGate(load_rules(killed / "rules.yaml"), killed / "gate.log") as g:
        p = subprocess.Popen([sys.executable, "-m", "copier", "run", "--run-dir", str(killed), "--port", str(g.port), "--pace-ms", "40"],
                             cwd=ROOT, stdout=subprocess.DEVNULL)
        end = time.monotonic() + 30
        while g.gate.book.last_seq < 8 and time.monotonic() < end:
            time.sleep(0.01)
        p.send_signal(sig_mod.SIGKILL)
        p.wait()
        mid = g.gate.book.last_seq
        assert 8 <= mid < 60, "the copier must die mid-feed for this test to mean anything"
        run_follower(killed, g.port)
    assert (killed / "gate.log").read_bytes().replace(killed.name.encode(), b"") == (clean / "gate.log").read_bytes().replace(clean.name.encode(), b"")
    assert (killed / "copier.log").read_bytes() == (clean / "copier.log").read_bytes()
    ids = [e.client_order_id for e in scan(killed / "gate.log")]
    skipped = [l for l in ledger.read(killed / "copier.log") if l["reason_code"] != "SIZE_CLIPPED"]
    assert len(ids) == len(set(ids)) == 60 - len(skipped)


def test_same_feed_rules_and_data_give_byte_identical_logs(tmp_path):
    outs = []
    for n in ("one", "two"):
        d = fixture_run(tmp_path, name=n, overrides={"max_daily_loss": 300, "max_position_per_symbol": 12})
        follow(d)
        outs.append(((d / "gate.log").read_bytes(), (d / "copier.log").read_bytes(), (d / "rules.yaml").read_bytes()))
    assert outs[0] == outs[1] and outs[0][0]


@settings(max_examples=25, deadline=None, suppress_health_check=list(HealthCheck))
@given(items=st.lists(st.tuples(st.sampled_from(["AAPL", "MSFT", "NVDA", "TSLA", "SPY"]), st.sampled_from(["BUY", "SELL"]),
                                st.integers(1, 60), st.integers(-400, 400), st.sampled_from(["local", "local", "local", "mallory"]),
                                st.integers(0, 9)), min_size=1, max_size=40),
       limit=st.integers(1, 30), band=st.integers(1, 6), scale=st.sampled_from([(1, 1), (1, 2), (3, 2), (1, 7)]))
def test_property_every_accepted_order_satisfies_the_followers_limits(items, limit, band, scale):
    data = datamod.load_fixture()
    with tempfile.TemporaryDirectory() as tmp:
        text, rules = build_rules(BASE, data.anchors, {"max_position_per_symbol": limit, "price_band_pct": band, "max_daily_loss": 4000})
        (Path(tmp) / "rules.yaml").write_text(text)
        incoming = []
        for n, (sym, side, qty, off_bp, signer, skip) in enumerate(items, 1):
            price = max(1, data.anchors[sym] * (10_000 + off_bp * 10) // 10_000)
            leader_seq = n + (3 if skip == 0 else 0)                   # sometimes a gap
            incoming.append(Incoming(signer, sigd(leader_seq, symbol=sym, side=side, qty=qty, price=price, ts=OPEN_MS + 60_000 * n), f"l{n}"))
        with ThreadGate(rules, Path(tmp) / "gate.log") as g:
            c = GateClient(g.port)
            lg = ledger.CopierLog(Path(tmp) / "copier.log")
            Copier(FollowerConfig(scale_num=scale[0], scale_den=scale[1]), rules.symbols, c.submit, lg).run(incoming)
            c.close()
        led, accepted, signers = Ledger(), 0, {"local"}
        for e in scan(Path(tmp) / "gate.log"):
            if e.decision != "ACCEPT":
                continue
            accepted += 1
            ref = rules.symbols[e.symbol].reference_price
            assert abs(e.price - ref) * 100 <= ref * band                                     # price band
            before = led.position(e.symbol)
            signed = e.qty if e.side == "BUY" else -e.qty
            assert abs(before + signed) <= limit or abs(before + signed) <= abs(before)       # position limit (closing always ok)
            assert before + signed >= 0                                                       # close-only: never short
            if abs(before + signed) > abs(before):
                assert led.realized_gross - led.fees > -rules.max_daily_loss                   # daily loss only blocks new risk
            led.fill(e.symbol, e.side, e.qty, e.price, rules.fees.of(e.qty))
        for sid, code in [(l["signal_id"], l["reason_code"]) for l in ledger.read(Path(tmp) / "copier.log")]:
            assert code != "UNTRUSTED_LEADER" or sid.startswith("mallory:")
        sent = {e.client_order_id for e in scan(Path(tmp) / "gate.log")}
        assert not any(order_id_for(f"mallory:{sigd(n)['leader_seq']}") in sent for n in range(1, 60))


# ------------------------------------------------------------------ copier.log
def test_copier_log_is_chained_and_tamper_evident(tmp_path):
    p = tmp_path / "copier.log"
    lg = ledger.CopierLog(p)
    for i in range(3):
        lg.append(f"local:{i}", "FEED_GAP", f"text {i}")
    lg.close()
    raw = p.read_bytes()
    assert len(ledger.read(p)) == 3
    p.write_bytes(raw.replace(b"text 1", b"text X"))
    with pytest.raises(ledger.LedgerError, match="line 2"):
        ledger.read(p)
    p.write_bytes(b"".join(raw.splitlines(True)[::-1]))
    with pytest.raises(ledger.LedgerError):
        ledger.read(p)
    p.write_bytes(raw + b'{"seq":4')                      # torn tail from a kill -9: ignored, then repaired on open
    lg = ledger.CopierLog(p)
    assert len(lg.lines) == 3 and lg.append("local:0", "FEED_GAP", "text 0") is False
    lg.close()
    assert p.read_bytes() == raw
    lg = ledger.CopierLog(p)
    lg.append("local:0", "FEED_GAP", "text 0")
    with pytest.raises(ledger.LedgerError, match="differs"):
        lg.append("local:9", "FEED_GAP", "other")


# ------------------------------------------------------------------ explanations
def tight_rules(**kw):
    base = load_rules(BASE).model_dump()
    base["symbols"] = {"AAPL": {"reference_price": 19000}, "MSFT": {"reference_price": 42000}}
    base.update(kw)
    return Rules.model_validate(base)


def independent(events, rules):
    """Recompute, with nothing but Ledger and lists, the numbers each REJECT explanation must contain."""
    led, times, out, streak = Ledger(), [], {}, []
    for e in events:
        if e.decision == "REJECT":
            pos = led.position(e.symbol)
            new = pos + (e.qty if e.side == "BUY" else -e.qty)
            win = len([t for t in times if t > e.ts - 60 * 10**9])
            out[e.seq] = dict(pos=pos, new=new, window=win, loss=-(led.realized_gross - led.fees))
        if e.reason_code != "RATE_LIMIT":
            times.append(e.ts)
        if e.decision == "ACCEPT":
            led.fill(e.symbol, e.side, e.qty, e.price, rules.fees.of(e.qty))
    return out


def test_every_reject_is_explained_with_numbers_matching_an_independent_replay(tmp_path):
    rules = tight_rules(max_position_per_symbol=20, max_daily_loss=100_000, max_orders_per_minute=9,
                        loss_streak_cooldown={"losses": 2, "cooldown_seconds": 300})
    lines = [line("a1", "BUY", "AAPL", 10, 19000), line("a2", "BUY", "AAPL", 15, 19000),     # MAX_POSITION (25 > 20)
             line("a3", "BUY", "AAPL", 5, 25000),                                             # PRICE_BAND
             line("a4", "BUY", "XYZ", 5, 100), line("a5", "SELL", "AAPL", 10, 18900),         # BAD_ORDER; loss close
             line("a6", "BUY", "AAPL", 10, 19000), line("a7", "SELL", "AAPL", 10, 18900),     # 2nd loss -> cooldown
             line("a8", "BUY", "AAPL", 1, 19000),                                             # LOSS_STREAK_COOLDOWN
             line("a9", "SELL", "AAPL", 1, 19000)] + [line(f"b{i}", "BUY", "MSFT", 1, 42000) for i in range(6)]
    out, gate = run(run_gate(tmp_path / "g.log", lines, rules=rules))
    events = list(scan(tmp_path / "g.log"))
    codes = {e.reason_code for e in events if e.decision == "REJECT"}
    assert {"MAX_POSITION", "PRICE_BAND", "BAD_ORDER", "LOSS_STREAK_COOLDOWN", "RATE_LIMIT"} <= codes
    exps = explain_events(events, rules)
    assert [e["seq"] for e in exps] == [e.seq for e in events if e.decision == "REJECT"]
    ind = independent(events, rules)
    for x in exps:
        i = ind[x["seq"]]
        assert x["replay_agrees"] and x["text"]
        if x["reason_code"] == "MAX_POSITION":
            assert (x["before_n"], x["after_n"], x["limit_n"]) == (i["pos"], i["new"], 20) == (10, 25, 20)
        if x["reason_code"] == "RATE_LIMIT":
            assert (x["before_n"], x["after_n"], x["limit_n"]) == (i["window"], i["window"] + 1, 9)
        if x["reason_code"] == "PRICE_BAND":
            assert x["after_bp"] == (25000 - 19000) * 10_000 // 19000 and x["ref_n"] == 19000
        if x["reason_code"] == "LOSS_STREAK_COOLDOWN":
            assert len(x["causes"]) == 2 and "2 losing trades in a row triggered the cooldown" in x["cause_text"]
            assert x["causes"] == [e.seq for e in events if e.client_order_id in ("a5", "a7")]
    # a gate that keeps the daily-loss rule: a large loss, then new risk
    rules2 = tight_rules(max_daily_loss=200)
    run(run_gate(tmp_path / "g2.log", [line("c1", "BUY", "AAPL", 10, 19000), line("c2", "SELL", "AAPL", 10, 18900),
                                       line("c3", "BUY", "AAPL", 1, 19000)], rules=rules2))
    ev2 = list(scan(tmp_path / "g2.log"))
    (x,) = explain_events(ev2, rules2)
    assert x["reason_code"] == "MAX_DAILY_LOSS" and x["causes"] == [2]
    assert x["before_n"] == independent(ev2, rules2)[3]["loss"] == 1000 + 2 * 50 and x["limit_n"] == 200


@settings(max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))
@given(st.lists(st.tuples(st.sampled_from(["BUY", "SELL"]), st.integers(1, 12), st.integers(18500, 19500)), min_size=1, max_size=40))
def test_property_explained_numbers_match_independent_replay(orders):
    rules = tight_rules(max_position_per_symbol=8, max_daily_loss=500, max_orders_per_minute=12, price_band_pct=2,
                        loss_streak_cooldown={"losses": 2, "cooldown_seconds": 1})
    with tempfile.TemporaryDirectory() as tmp:
        lines = [line(f"o{n}", s, "AAPL", q, p) for n, (s, q, p) in enumerate(orders)]
        run(run_gate(Path(tmp) / "g.log", lines, rules=rules, clock=counter_clock(5 * 10**9)))
        events = list(scan(Path(tmp) / "g.log"))
        ind = independent(events, rules)
        for x in explain_events(events, rules):
            i = ind[x["seq"]]
            assert x["replay_agrees"]
            if x["reason_code"] == "MAX_POSITION":
                assert (x["before_n"], x["after_n"]) == (i["pos"], i["new"])
            if x["reason_code"] == "RATE_LIMIT":
                assert x["before_n"] == i["window"] >= rules.max_orders_per_minute
            if x["reason_code"] == "MAX_DAILY_LOSS":
                assert x["before_n"] == i["loss"] >= rules.max_daily_loss


def test_explanations_are_deterministic_and_origin_independent(tmp_path):
    d = fixture_run(tmp_path, overrides={"max_daily_loss": 300})
    follow(d)
    rules = load_rules(d / "rules.yaml")
    a, b = explain_log(d / "gate.log", rules), explain_log(d / "gate.log", rules)
    assert a and orjson.dumps(a) == orjson.dumps(b)
    copy = tmp_path / "elsewhere.log"
    copy.write_bytes((d / "gate.log").read_bytes())
    assert orjson.dumps(explain_log(copy, rules)) == orjson.dumps(a)             # only the log + rules matter
    assert orjson.dumps(explain_copier_log(d / "copier.log")) == orjson.dumps(explain_copier_log(d / "copier.log"))
    out = subprocess.run([sys.executable, "-m", "auditor.explain", str(d)], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0 and "MAX_DAILY_LOSS" in out.stdout and out.stdout == subprocess.run(
        [sys.executable, "-m", "auditor.explain", str(d)], cwd=ROOT, capture_output=True, text=True).stdout


def test_explain_refuses_a_tampered_log(tmp_path):
    from core.logio import ChainError
    d = fixture_run(tmp_path, overrides={"max_daily_loss": 300})
    follow(d)
    raw = (d / "gate.log").read_bytes()
    (d / "gate.log").write_bytes(raw.replace(b'"qty":10', b'"qty":11', 1))
    with pytest.raises(ChainError):
        explain_log(d / "gate.log", load_rules(d / "rules.yaml"))


# ------------------------------------------------------------------ chain feed wiring
LEADER_KEY = "CfUDMw61yyZKofYuptmTNLcaxFZTaytKV3HAhkh7Ls7w"      # a public address (the project's throwaway devnet leader)


def fake_chain(monkeypatch, result):
    import copier.__main__ as cm

    class Stub:
        def __init__(self, rpc, allowlist, cache, **kw):
            assert tuple(allowlist) == (LEADER_KEY,)

        def read(self):
            return result
    monkeypatch.setattr(cm, "ChainFeed", Stub)
    return cm


def run_cli(cm, tmp_path, *extra):
    return cm.main(["follow", "--feed", "chain", "--leader-address", LEADER_KEY, "--offline", "--runs-dir", str(tmp_path / "r"),
                    "--scale", "1/1", "--drawdown-stop", "1000000", *extra])


def test_chain_feed_signals_are_copied_with_the_leaders_key_as_the_only_trusted_signer(tmp_path, monkeypatch, capsys):
    from leaders.feed import FeedItem, FeedResult
    data = datamod.load_fixture()
    items = [FeedItem(s, LEADER_KEY, 100 + s.leader_seq, 0, f"sig{s.leader_seq}")
             for s in generate(data.bars, "momentum", "synthetic", qty=10)]
    items.append(FeedItem(items[0].signal.__class__(**{**items[0].signal.to_dict(), "leader_seq": 99}), "ImpostorKey1111", 500, 0, "sigX"))
    cm = fake_chain(monkeypatch, FeedResult(items, "chain", [], ["read from 1 allowlisted signer(s)"]))
    assert run_cli(cm, tmp_path) == 0
    out = capsys.readouterr().out
    (d,) = (tmp_path / "r").iterdir()
    m = orjson.loads((d / "manifest.json").read_bytes())
    assert "FEED: source: chain" in out and m["feed"]["source"] == "chain" and m["feed"]["leader"] == LEADER_KEY
    assert m["follower"]["allowlist"] == [LEADER_KEY]
    assert [i.signer for i in read_local(d / "feed.jsonl")][-1] == "ImpostorKey1111"
    skips = {l["signal_id"]: l["reason_code"] for l in ledger.read(d / "copier.log")}
    assert skips[f"ImpostorKey1111:99"] == "UNTRUSTED_LEADER"
    sent = {e.client_order_id for e in scan(d / "gate.log")}
    assert order_id_for(f"ImpostorKey1111:99") not in sent and order_id_for(f"{LEADER_KEY}:{items[0].signal.leader_seq}") in sent


def test_chain_failure_falls_back_to_the_cache_then_the_local_feed_and_says_so(tmp_path, monkeypatch, capsys):
    from leaders.feed import FeedItem, FeedResult
    data = datamod.load_fixture()
    sigs = list(generate(data.bars, "momentum", "synthetic", qty=10))
    cached = FeedResult([FeedItem(s, LEADER_KEY, 1, 0, f"s{s.leader_seq}") for s in sigs], "cache", [], ["network failure; showing CACHED data"])
    cm = fake_chain(monkeypatch, cached)
    assert run_cli(cm, tmp_path / "a") == 0
    assert "source: chain cache (stale)" in capsys.readouterr().out
    cm = fake_chain(monkeypatch, FeedResult([], "unavailable", [], ["network failure (rpc answered HTTP 429); no usable cache"]))
    assert run_cli(cm, tmp_path / "b") == 0
    out = capsys.readouterr().out
    assert "source: local feed" in out and "asked for chain" in out and "429" in out
    (d,) = (tmp_path / "b" / "r").iterdir()
    m = orjson.loads((d / "manifest.json").read_bytes())
    assert m["feed"]["source"] == "local feed" and m["feed"]["requested"] == "chain" and m["follower"]["allowlist"] == ["local"]
    assert sum(1 for _ in scan(d / "gate.log")) > 0
