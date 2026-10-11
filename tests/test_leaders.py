"""Leaders + marketdata tests. No network, no keys: fake RPC, fake providers, synthetic bars."""

import ast
import hashlib
import json
import random
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from leaders.feed import ChainFeed, LocalFeed
from leaders.publisher import Publisher
from leaders.runner import LookAheadError, generate
from leaders.signal import MAX_MEMO_BYTES, Signal, SignalError, parse_memo
from leaders.solana import (DEVNET_GENESIS, MEMO_PROGRAM, Keypair, MainnetWriteRefused, RpcClient, RpcError, b58decode,
                            b58encode, is_devnet_url, memo_transaction)
from marketdata import Bar, NoDataError, NotEnabledError
from marketdata.calendar import (CalendarRangeError, in_session, is_early_close, is_trading_day, session, session_ms,
                                 trading_days)
from marketdata.providers import YFinanceHistorical, AlpacaHistorical
from marketdata.replay import ReplayBackend
from marketdata.stubs import AlpacaLive, Pyth, XStocks

ROOT = Path(__file__).resolve().parent.parent
DEVNET = "https://api.devnet.solana.com"


# ---------- calendar ----------
def utc(d):
    return tuple(x.strftime("%Y-%m-%d %H:%M") for x in session(d))


def test_dst_session_times():
    assert utc(date(2026, 3, 6)) == ("2026-03-06 14:30", "2026-03-06 21:00")   # EST, UTC-5
    assert utc(date(2026, 3, 9)) == ("2026-03-09 13:30", "2026-03-09 20:00")   # EDT after spring forward (Mar 8)
    assert utc(date(2026, 10, 30)) == ("2026-10-30 13:30", "2026-10-30 20:00")
    assert utc(date(2026, 11, 2)) == ("2026-11-02 14:30", "2026-11-02 21:00")   # EST after fall back (Nov 1)


def test_weekends_holidays_early_closes():
    assert not is_trading_day(date(2026, 3, 7)) and not is_trading_day(date(2026, 3, 8))
    assert not is_trading_day(date(2026, 7, 3)) and is_trading_day(date(2026, 7, 2))   # Jul 4 2026 is a Saturday
    assert not is_trading_day(date(2026, 4, 3))                                        # Good Friday
    assert not is_trading_day(date(2025, 1, 9))
    assert session(date(2026, 12, 25)) is None
    assert is_early_close(date(2026, 11, 27)) and not is_early_close(date(2026, 11, 26))
    assert utc(date(2026, 11, 27)) == ("2026-11-27 14:30", "2026-11-27 18:00")          # 13:00 EST
    assert utc(date(2025, 7, 3)) == ("2025-07-03 13:30", "2025-07-03 17:00")            # 13:00 EDT
    assert not is_early_close(date(2026, 12, 26))                                       # weekend is never an early close


def test_calendar_refuses_to_guess_outside_table():
    with pytest.raises(CalendarRangeError):
        is_trading_day(date(2030, 1, 2))
    assert len(list(trading_days(date(2026, 11, 23), date(2026, 11, 30)))) == 5        # Mon,Tue,Wed,Fri(early),Mon


def test_in_session_edges():
    o, c = session_ms(date(2026, 3, 9))
    assert in_session(o) and in_session(c - 1) and not in_session(c) and not in_session(o - 1)


# ---------- stubs / providers ----------
@pytest.mark.parametrize("cls", [AlpacaLive, XStocks, Pyth])
def test_stubs_not_enabled_and_no_network(cls, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network used"))
    with pytest.raises(NotEnabledError, match="not enabled"):
        cls().get_bars(["AAPL"], date(2026, 1, 5), date(2026, 1, 6), "5m")


def fake_rows(symbols, start, end, interval, base_ms=None):
    """One regular-session day of 5m rows starting 2026-03-09 13:30Z (EDT), prices that wiggle deterministically."""
    t0 = int(datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
    rows = []
    for s in symbols:
        for i in range(78):
            c = 10000 + (i * 7) % 50
            rows.append((s, t0 + i * 300_000, c, c + 5, c - 5, c, 100))
    return rows, "UTC"


class FakeProvider:
    name = "fake"

    def __init__(self, rows=None):
        self.calls, self.rows = 0, rows

    def fetch(self, symbols, start, end, interval):
        self.calls += 1
        if self.rows == "empty":
            return [], "UTC"
        return (self.rows or fake_rows(symbols, start, end, interval)[0]), "UTC"


def test_replay_caches_and_labels(tmp_path):
    p = FakeProvider()
    md = ReplayBackend(p, tmp_path, now=lambda: datetime(2026, 3, 10, tzinfo=timezone.utc))
    a = md.get_bars(["SPY", "AAPL"], date(2026, 3, 9), date(2026, 3, 9), "5m")
    b = md.get_bars(["AAPL", "SPY"], date(2026, 3, 9), date(2026, 3, 9), "5m")
    assert p.calls == 1 and not a.from_cache and b.from_cache
    assert a.bars == b.bars and len(a.bars) == 156
    assert (a.provider, a.interval, a.symbols, a.timezone, a.retrieved_at) == ("fake", "5m", ["AAPL", "SPY"], "UTC", "2026-03-10T00:00:00Z")
    meta = json.loads(next(tmp_path.glob("*.json")).read_text())["meta"]
    assert {"provider", "symbols", "interval", "start", "end", "timezone", "retrieved_at"} <= set(meta)
    assert a.bars[0].ts == int(datetime(2026, 3, 9, 13, 35, tzinfo=timezone.utc).timestamp() * 1000)  # ts is the bar END


def test_replay_no_data_never_fabricates(tmp_path):
    with pytest.raises(NoDataError, match="nothing was fabricated"):
        ReplayBackend(FakeProvider("empty"), tmp_path).get_bars(["AAPL"], date(2026, 3, 9), date(2026, 3, 9), "5m")
    assert not list(tmp_path.glob("*.json"))
    # rows only on a weekend / outside hours are dropped, leaving nothing
    t = int(datetime(2026, 3, 8, 15, tzinfo=timezone.utc).timestamp() * 1000)
    with pytest.raises(NoDataError):
        ReplayBackend(FakeProvider([("AAPL", t, 1, 1, 1, 1, 1)]), tmp_path).get_bars(["AAPL"], date(2026, 3, 8), date(2026, 3, 8), "5m")


def test_yfinance_intraday_lookback_is_explicit():
    y = YFinanceHistorical(download=lambda *a: ([], "UTC"), today=date(2026, 3, 10))
    with pytest.raises(NoDataError, match="last 60 days"):
        y.fetch(["AAPL"], date(2025, 1, 6), date(2025, 1, 7), "5m")
    assert y.fetch(["AAPL"], date(2026, 3, 2), date(2026, 3, 3), "5m") == ([], "UTC")


def test_alpaca_uses_data_endpoint_only_and_keys_stay_out_of_errors():
    seen = {}

    class R:
        status_code = 200
        def json(self):
            return {"bars": {"AAPL": [{"t": "2026-03-09T13:30:00Z", "o": 190.5, "h": 191, "l": 190, "c": 190.75, "v": 5}]}}

    class H:
        def get(self, url, params, headers):
            seen["url"] = url
            return R()

    rows, tz = AlpacaHistorical("KEYID", "SECRET", http=H()).fetch(["AAPL"], date(2026, 3, 9), date(2026, 3, 10), "5m")
    assert seen["url"].startswith("https://data.alpaca.markets/v2/stocks/bars") and rows[0][2:6] == (19050, 19100, 19000, 19075)

    class Boom:
        def get(self, *a, **k):
            raise RuntimeError("boom KEYID SECRET")

    with pytest.raises(Exception) as e:
        AlpacaHistorical("KEYID", "SECRET", http=Boom()).fetch(["AAPL"], date(2026, 3, 9), date(2026, 3, 10), "5m")
    assert "KEYID" not in str(e.value) and "SECRET" not in str(e.value)


# ---------- strategies: determinism and no look-ahead ----------
def synth_bars(seed=1, days=3, symbols=("AAPL", "SPY")):
    rnd = random.Random(seed)
    out, d = [], date(2026, 3, 9)
    price = {s: 10000 for s in symbols}
    while days:
        if is_trading_day(d):
            o, _ = session_ms(d)
            for i in range(78):
                for s in symbols:
                    price[s] = max(100, price[s] + rnd.randint(-30, 32))
                    p = price[s]
                    out.append(Bar(s, o + (i + 1) * 300_000, p, p + 4, p - 4, p, rnd.randint(1, 500)))
            days -= 1
        d = date.fromordinal(d.toordinal() + 1)
    return sorted(out, key=lambda b: (b.ts, b.symbol))


@pytest.mark.parametrize("name", ["orb", "momentum", "meanrev"])
def test_determinism_and_integers(name):
    bars = synth_bars()
    a, b = list(generate(bars, name, "fake")), list(generate(bars, name, "fake"))
    assert a == b and a, "strategy should trade on this data"
    for s in a:
        assert all(type(getattr(s, k)) is int for k in ("qty", "ref_price", "data_ts", "leader_ts", "leader_seq"))
        assert s.data_ts <= s.leader_ts
        parse_memo(s.to_memo())  # every signal is valid v1


@pytest.mark.parametrize("name", ["orb", "momentum", "meanrev"])
def test_no_lookahead_prefix_invariance_and_lazy_feed(name):
    bars = synth_bars(seed=3)
    full = list(generate(bars, name, "fake"))
    pulled = []

    def lazy():
        for bar in bars:
            pulled.append(bar.ts)
            yield bar

    for cut in (len(bars) // 3, len(bars) // 2, len(bars) - 5):
        t = bars[cut].ts
        # the signals up to time t from a dataset that ENDS at t must equal those from the full dataset
        assert [s for s in full if s.leader_ts <= t] == list(generate([b for b in bars if b.ts <= t], name, "fake"))
    for s in generate(lazy(), name, "fake"):
        # when a signal is emitted, no bar past its decision time has been pulled from the feed yet
        assert max(pulled) <= s.leader_ts
    # data stamped later than the decision time is never used: poisoning the future changes nothing before it
    cut = len(bars) // 2
    poisoned = bars[:cut] + [Bar(b.symbol, b.ts, 1, 10**8, 1, 10**8 if i % 2 else 1, 1) for i, b in enumerate(bars[cut:])]
    t = bars[cut - 1].ts
    assert [s for s in generate(poisoned, name, "fake") if s.leader_ts <= t] == [s for s in full if s.leader_ts <= t]


def test_runner_rejects_out_of_order_bars():
    bars = synth_bars()
    with pytest.raises(LookAheadError):
        list(generate([bars[5], bars[0]], "momentum", "fake"))


# ---------- signal format ----------
GOOD = dict(v=1, leader_seq=1, symbol="AAPL", side="BUY", qty=10, ref_price=19000, data_ts=1, data_source="alpaca", leader_ts=2)


@pytest.mark.parametrize("patch,reason", [
    ({"v": 2}, "version"), ({"symbol": "GME"}, "symbol"), ({"side": "HOLD"}, "side"), ({"qty": 0}, "qty"),
    ({"qty": 1.5}, "qty"), ({"qty": True}, "qty"), ({"ref_price": 190.5}, "ref_price"), ({"ref_price": -1}, "ref_price"),
    ({"leader_seq": "1"}, "leader_seq"), ({"data_source": "Bad Source!"}, "data_source"), ({"extra": 1}, "unexpected"),
])
def test_signal_validation_reasons(patch, reason):
    with pytest.raises(SignalError, match=reason):
        parse_memo(json.dumps({**GOOD, **patch}))


def test_signal_malformed_and_oversized():
    for raw, why in [(b"not json", "JSON"), (b"[1]", "object"), (b"x" * (MAX_MEMO_BYTES + 1), "limit")]:
        with pytest.raises(SignalError, match=why):
            parse_memo(raw)
    d = dict(GOOD)
    del d["qty"]
    with pytest.raises(SignalError, match="missing"):
        parse_memo(json.dumps(d))
    assert parse_memo(json.dumps(GOOD)).qty == 10


# ---------- solana bits / mainnet guard ----------
def test_base58_and_tx_shape():
    assert b58encode(b"\0\0\x01") == "112" and b58decode("112") == b"\0\0\x01"
    kp = Keypair.generate()
    assert b58decode(kp.address) == kp.public and len(b58decode(MEMO_PROGRAM)) == 32
    tx, sig = memo_transaction(kp, b"hi", b58encode(bytes(32)))
    assert tx[0] == 1 and tx[1:65] == b58decode(sig) and b"hi" in tx
    assert "seed" not in repr(kp) and kp.address in repr(kp)


def test_keypair_file_mode_and_no_overwrite(tmp_path):
    kp = Keypair.generate()
    p = tmp_path / "k" / "leader.json"
    kp.save(p)
    assert oct(p.stat().st_mode & 0o777) == "0o600" and Keypair.load(p).address == kp.address
    with pytest.raises(FileExistsError):
        kp.save(p)


@pytest.mark.parametrize("url,ok", [
    ("https://api.devnet.solana.com", True), ("https://devnet.helius-rpc.com/?api-key=x", True),
    ("https://api.mainnet-beta.solana.com", False), ("https://api.testnet.solana.com", False),
    ("http://localhost:8899", False), ("https://evil.com/devnet", False), ("ftp://api.devnet.solana.com", False),
    ("", False), ("not a url", False),
])
def test_devnet_url_check(url, ok):
    assert is_devnet_url(url) is ok


def test_mainnet_write_guard(tmp_path):
    sent = []
    rpc = RpcClient("https://api.mainnet-beta.solana.com", post=lambda u, p: sent.append(p) or {"result": 1})
    for method in ("sendTransaction", "requestAirdrop"):
        with pytest.raises(MainnetWriteRefused):
            rpc.call(method, [])
    assert not sent                                            # refused before any request was made
    with pytest.raises(MainnetWriteRefused):
        Publisher(tmp_path / "r", rpc, Keypair.generate())     # refuses to start at all
    assert not (tmp_path / "r" / "feed.jsonl").exists()
    # right-looking hostname but the cluster says it is not devnet: still refused, and nothing is sent
    fake = FakeChain(genesis="5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d")
    pub = Publisher(tmp_path / "r2", RpcClient(DEVNET, post=fake.post), Keypair.generate(), sleep=lambda s: None)
    with pytest.raises(MainnetWriteRefused):
        pub.publish(sample_signal(1))
    assert not fake.sent


# ---------- fake chain ----------
class FakeChain:
    """Fake JSON-RPC node: records sent txs and can serve them back as parsed transactions."""

    def __init__(self, genesis=DEVNET_GENESIS, fail_sends=0, down=False):
        self.genesis, self.fail_sends, self.down, self.sent, self.txs, self.calls = genesis, fail_sends, down, [], {}, []

    def post(self, url, p):
        m, a = p["method"], p["params"]
        self.calls.append(m)
        if self.down:
            raise ConnectionError("down")
        if m == "getGenesisHash":
            return {"result": self.genesis}
        if m == "getLatestBlockhash":
            return {"result": {"value": {"blockhash": b58encode(bytes(32))}}}
        if m == "sendTransaction":
            if self.fail_sends:
                self.fail_sends -= 1
                return {"error": {"message": "429 too many requests"}}
            self.sent.append(a[0])
            return {"result": "sig"}
        if m == "getSignaturesForAddress":
            return {"result": [{"signature": s, "slot": t["slot"], "err": None} for s, t in self.txs.items()
                               if t["transaction"]["message"]["accountKeys"][0]["pubkey"] == a[0]
                               or a[0] in [k["pubkey"] for k in t["transaction"]["message"]["accountKeys"]]]}
        if m == "getTransaction":
            return {"result": self.txs.get(a[0])}
        if m == "requestAirdrop":
            return {"result": "airdrop"}
        raise AssertionError(m)

    def add(self, sig, slot, payer, memo, program=MEMO_PROGRAM, n_memos=1):
        ix = {"programId": program, "program": "spl-memo", "parsed": memo}
        self.txs[sig] = {"slot": slot, "meta": {"err": None},
                         "transaction": {"signatures": [sig],
                                         "message": {"accountKeys": [{"pubkey": payer, "signer": True}], "instructions": [ix] * n_memos}}}


def sample_signal(i, **kw):
    d = dict(leader_seq=i, symbol="AAPL", side="BUY", qty=10, ref_price=19000, data_ts=1000 + i, data_source="fake", leader_ts=1000 + i)
    return Signal(**{**d, **kw})


# ---------- publisher ----------
def test_publisher_throttle_backoff_and_feed(tmp_path):
    clock = [0.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    fake = FakeChain(fail_sends=2)
    pub = Publisher(tmp_path, RpcClient(DEVNET, post=fake.post), Keypair.generate(), clock=lambda: clock[0], sleep=sleep)
    lines = [pub.publish(sample_signal(i)) for i in (1, 2, 3)]
    assert [l["via"] for l in lines] == ["chain"] * 3 and len(fake.sent) == 3
    assert 1.0 in sleeps and 2.0 in sleeps                       # exponential backoff after the two 429s
    assert fake.calls.count("getGenesisHash") == 1               # cluster check once, before the first write
    feed = LocalFeed(tmp_path / "feed.jsonl").read()
    assert [i.signal.leader_seq for i in feed.items] == [1, 2, 3] and feed.source == "local"


def test_publisher_throttles_to_one_tx_per_second(tmp_path):
    clock = [0.0]
    stamps = []
    fake = FakeChain()
    orig = fake.post
    fake.post = lambda u, p: (stamps.append(clock[0]) if p["method"] == "sendTransaction" else None) or orig(u, p)
    pub = Publisher(tmp_path, RpcClient(DEVNET, post=fake.post), Keypair.generate(),
                    clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
    for i in range(1, 5):
        pub.publish(sample_signal(i))
    assert all(b - a >= 1.0 for a, b in zip(stamps, stamps[1:])) and len(stamps) == 4


def test_publisher_falls_back_to_local_feed_and_labels_it(tmp_path):
    fake = FakeChain(down=True)
    pub = Publisher(tmp_path, RpcClient(DEVNET, post=fake.post), Keypair.generate(), sleep=lambda s: None, max_retries=2)
    lines = [pub.publish(sample_signal(i)) for i in (1, 2)]
    assert [l["via"] for l in lines] == ["local-only"] * 2 and not pub.chain_ok
    n_calls = len(fake.calls)
    pub.publish(sample_signal(3))
    assert len(fake.calls) == n_calls                              # chain stays off; no hammering a dead RPC
    r = LocalFeed(tmp_path / "feed.jsonl").read()
    assert len(r.items) == 3 and any("chain publishing was off" in n for n in r.notes)


def test_publisher_without_chain_is_local_only(tmp_path):
    pub = Publisher(tmp_path)
    assert pub.publish(sample_signal(1))["via"] == "local-only" and (tmp_path / "feed.jsonl").exists()


def test_local_feed_rejects_bad_lines(tmp_path):
    p = tmp_path / "feed.jsonl"
    good = json.dumps({"signal": GOOD, "via": "local-only", "tx": None})
    bad_json, bad_sig = "{oops", json.dumps({"signal": {**GOOD, "qty": -1}})
    p.write_text("\n".join([good, bad_json, bad_sig, good]) + "\n")
    r = LocalFeed(p).read()
    assert len(r.items) == 1
    reasons = dict(r.rejected)
    assert reasons["line 2"] == "not valid JSON" and "qty" in reasons["line 3"] and "duplicate" in reasons["line 4"]


# ---------- chain feed ----------
def two_signers():
    return Keypair.generate().address, Keypair.generate().address


def test_chain_feed_rejects_with_reasons(tmp_path):
    leader, rogue = two_signers()
    f = FakeChain()
    memo = lambda i: json.dumps({**GOOD, "leader_seq": i})
    f.add("sigA", 10, leader, memo(1))
    f.add("sigB", 11, rogue, memo(2))                                        # wrong signer, but mentions nothing of leader
    f.add("sigC", 12, leader, "garbage")                                     # malformed
    f.add("sigD", 13, leader, "x" * 1000)                                    # oversized
    f.add("sigE", 14, leader, json.dumps({**GOOD, "qty": 1.5}))              # invalid field
    f.add("sigF", 15, leader, memo(1))                                       # duplicate leader_seq
    f.add("sigG", 16, leader, memo(3), program="11111111111111111111111111111111")  # not the memo program
    f.add("sigH", 17, leader, memo(4), n_memos=2)                            # two memos
    # the rogue tx also lists the leader's key as a non-fee-payer account
    f.txs["sigB"]["transaction"]["message"]["accountKeys"].append({"pubkey": leader, "signer": False})
    r = ChainFeed(RpcClient(DEVNET, post=f.post), [leader], tmp_path / "c.json").read()
    assert [i.signature for i in r.items] == ["sigA"] and r.source == "chain"
    why = dict(r.rejected)
    assert "allowlist" in why["sigB"] and "JSON" in why["sigC"] and "oversized" in why["sigD"]
    assert "qty" in why["sigE"] and "duplicate" in why["sigF"] and "exactly one memo" in why["sigG"] and "exactly one memo" in why["sigH"]


def test_chain_feed_order_stable_under_shuffled_arrival(tmp_path):
    leader, _ = two_signers()
    entries = [(f"sig{n:02d}", 100 + n // 3, leader, json.dumps({**GOOD, "leader_seq": n})) for n in range(1, 13)]
    results = []
    for seed in range(8):
        shuffled = entries[:]
        random.Random(seed).shuffle(shuffled)
        f = FakeChain()
        for e in shuffled:
            f.add(*e)
        r = ChainFeed(RpcClient(DEVNET, post=f.post), [leader], tmp_path / f"c{seed}.json").read()
        results.append([(i.slot, i.index, i.signature) for i in r.items])
    assert all(x == results[0] for x in results) and results[0] == sorted(results[0])


def test_chain_feed_falls_back_to_cache_and_says_so(tmp_path):
    leader, _ = two_signers()
    f = FakeChain()
    f.add("sigA", 10, leader, json.dumps(GOOD))
    cache = tmp_path / "c.json"
    ok = ChainFeed(RpcClient(DEVNET, post=f.post), [leader], cache).read()
    assert ok.source == "chain"
    f.down = True
    r = ChainFeed(RpcClient(DEVNET, post=f.post), [leader], cache).read()
    assert r.source == "cache" and [i.signature for i in r.items] == ["sigA"] and "CACHED" in r.notes[0] and "network failure" in r.notes[0]
    none = ChainFeed(RpcClient(DEVNET, post=f.post), [leader], tmp_path / "missing.json").read()
    assert none.source == "unavailable" and none.items == [] and "no usable cache" in none.notes[0]


def test_published_tx_round_trips_through_memo_format(tmp_path):
    kp = Keypair.generate()
    f = FakeChain()
    pub = Publisher(tmp_path, RpcClient(DEVNET, post=f.post), kp, sleep=lambda s: None)
    s = sample_signal(1)
    pub.publish(s)
    wire = __import__("base64").b64decode(f.sent[0])
    assert s.to_memo() in wire and kp.public in wire and parse_memo(s.to_memo()) == s


# ---------- setup command ----------
def test_setup_refuses_non_devnet_and_reports_faucet_failure(tmp_path):
    from leaders.__main__ import cmd_setup
    out = []
    cfg = {"rpc": "https://api.mainnet-beta.solana.com", "keypair": str(tmp_path / "k.json")}
    assert cmd_setup(cfg, out=out.append) == 2 and not (tmp_path / "k.json").exists() and "devnet" in out[0]
    cfg["rpc"] = DEVNET

    class NoFaucet(FakeChain):
        def post(self, u, p):
            return {"error": {"message": "airdrop limit reached"}} if p["method"] == "requestAirdrop" else super().post(u, p)

    out.clear()
    assert cmd_setup(cfg, RpcClient(DEVNET, post=NoFaucet().post), out=out.append) == 1
    text = "\n".join(out)
    assert "airdrop failed" in text and "faucet" in text and (tmp_path / "k.json").exists()
    seed = json.loads((tmp_path / "k.json").read_text())[:32]
    assert str(seed) not in text and "[" not in text            # the key is never printed
    out.clear()
    assert cmd_setup(cfg, RpcClient(DEVNET, post=FakeChain().post), out=out.append) == 0
    out.clear()
    down = FakeChain(down=True)
    assert cmd_setup(cfg, RpcClient(DEVNET, post=down.post), out=out.append) == 1 and "cannot reach" in out[0]


# ---------- isolation from the gate ----------
def test_new_code_never_imports_or_touches_gate(tmp_path):
    for pkg in ("leaders", "marketdata"):
        for f in (ROOT / pkg).glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
                for m in mods:
                    assert m.split(".")[0] not in ("gate", "core", "auditor", "bench", "api", "client"), f"{f}: imports {m}"
    # a full run writes feed.jsonl only; a pre-existing gate.log in the same dir stays byte-identical
    run = tmp_path / "run"
    run.mkdir()
    gate_log = run / "gate.log"
    gate_log.write_bytes(b'{"seq":1}\n')
    before = hashlib.sha256(gate_log.read_bytes()).hexdigest()
    pub = Publisher(run)
    for s in generate(synth_bars(), "momentum", "fake"):
        pub.publish(s)
    assert hashlib.sha256(gate_log.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in run.iterdir()) == ["feed.jsonl", "gate.log"]
