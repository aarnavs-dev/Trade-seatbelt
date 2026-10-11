"""Simulator tests: replayable streams, offline operation, provider fallback, and "it only talks to the gate"."""

import ast
import json
import socket
from pathlib import Path

import pytest

from auditor.replay import audit
from core.events import Order
from core.rules import load_rules
from sim import MAX_SPEED, PROFILES
from sim.__main__ import main as sim_main
from sim.model import DT, SIGMA, PriceModel
from sim.providers import (OFFLINE_ANCHORS, AlpacaProvider, OfflineProvider, ProviderError, YFinanceProvider,
                           alpaca_keys, fetch_with_fallback, to_cents)
from sim.runfiles import RunError, create_run, default_seed, read_json, run_dir_for, sha256_file
from sim.trader import OrderStream

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "rules.yaml"


def take(seed, profile, n=600, anchors=OFFLINE_ANCHORS):
    s = OrderStream(seed, profile, anchors, 100)
    return [s.next() for _ in range(n)]


def make_run(tmp_path, **kw):
    kw.setdefault("provider", "offline")
    return create_run(tmp_path / "runs", base_rules=RULES, root=ROOT, **kw)


# ---------------------------------------------------------------- the order stream
@pytest.mark.parametrize("profile", PROFILES)
def test_same_seed_gives_an_identical_stream(profile):
    assert take(1234, profile) == take(1234, profile)


@pytest.mark.parametrize("profile", PROFILES)
def test_different_seeds_give_different_streams(profile):
    assert take(1, profile, 100) != take(2, profile, 100)


def test_default_seeds_differ_between_runs(tmp_path):
    seeds = {read_json(make_run(tmp_path) / "manifest.json")["seed"] for _ in range(3)}
    assert len(seeds) == 3 and len({default_seed() for _ in range(5)}) == 5


@pytest.mark.parametrize("profile", PROFILES)
def test_every_order_is_valid_for_the_gate_and_ids_are_unique(profile):
    items = take(9, profile, 800)
    for it in items:
        Order.model_validate(it.order)     # strict: ints only, side/symbol/id shape
        assert it.order["price"] > 0 and it.order["qty"] > 0
    assert len({it.order["client_order_id"] for it in items}) == len(items)


def test_profiles_behave_as_described():
    tags = {p: [t for it in take(5, p, 3000) for t in it.tags] for p in PROFILES}
    assert "oversize" not in tags["disciplined"] and "off_market" not in tags["disciplined"]
    assert tags["impulsive"].count("oversize") > 100 and tags["impulsive"].count("off_market") > 100
    assert "revenge" in tags["tilt"] and "revenge" not in tags["impulsive"]
    assert all(any(t == "fat_finger" for t in tags[p]) for p in PROFILES)    # occasional fat fingers everywhere
    assert all(any(t == "burst" for t in tags[p]) for p in PROFILES)


def test_tilt_re_enters_faster_and_larger_after_a_loss():
    items = take(11, "tilt", 4000)
    revenge = [it for it in items if "revenge" in it.tags and "burst" not in it.tags]
    normal = [it for it in items if not it.tags]
    assert revenge and normal
    mean = lambda xs: sum(xs) / len(xs)
    assert mean([i.gap for i in revenge]) < 0.7 * mean([i.gap for i in normal])
    assert mean([i.order["qty"] for i in revenge]) > 1.3 * mean([i.order["qty"] for i in normal])


def test_the_stream_does_not_depend_on_pause_or_speed_or_the_gate():
    """OrderStream has no inputs besides its constructor, so pacing and replies cannot change it."""
    s = OrderStream(3, "tilt", OFFLINE_ANCHORS, 100)
    assert OrderStream.next.__code__.co_argcount == 1 and [s.next() for _ in range(50)] == take(3, "tilt", 50)


def test_price_model_is_seeded_positive_and_has_the_documented_volatility():
    import math
    a, b = PriceModel(5, OFFLINE_ANCHORS), PriceModel(5, OFFLINE_ANCHORS)
    rets, prev = [], a.px["AAPL"]
    for _ in range(20000):
        a.step(); b.step()
        rets.append(math.log(a.px["AAPL"] / prev)); prev = a.px["AAPL"]
    assert a.px == b.px and all(v > 0 for v in a.px.values())
    sd = (sum(r * r for r in rets) / len(rets)) ** 0.5
    assert abs(sd / (SIGMA * DT ** 0.5) - 1) < 0.05


# ---------------------------------------------------------------- providers
def test_offline_provider_works_with_no_network_and_no_keys(monkeypatch):
    def no_net(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(socket, "getaddrinfo", no_net)
    f = fetch_with_fallback("offline", list(OFFLINE_ANCHORS), "1d", env={})
    assert f.quotes.provider == "offline" and f.fallback_reason is None and len(f.quotes.anchors) == 8
    assert alpaca_keys({}, None) is None


def test_offline_provider_refuses_symbols_it_has_no_anchor_for():
    with pytest.raises(ProviderError, match="no built-in anchor"):
        OfflineProvider().fetch(["AAPL", "ZZZZ"], "1d")


def test_snapshot_and_manifest_are_written(tmp_path):
    d = make_run(tmp_path, seed=77, profile="tilt", symbols=["AAPL", "MSFT", "NVDA"])
    snap, man = read_json(d / "market_snapshot.json"), read_json(d / "manifest.json")
    assert snap["provider"] == "offline" and snap["symbols"] == ["AAPL", "MSFT", "NVDA"] and snap["interval"] == "1d"
    assert snap["timezone"] and snap["retrieved_at"] and snap["anchors_minor"] == {s: OFFLINE_ANCHORS[s] for s in snap["symbols"]}
    assert man["seed"] == 77 and man["profile"] == "tilt" and man["simulated"] is True and man["started_at"]
    assert man["rules_sha256"] == sha256_file(d / "rules.yaml") and man["base_rules_sha256"] == sha256_file(RULES)
    assert man["snapshot_sha256"] == sha256_file(d / "market_snapshot.json") and man["git"]["commit"]


def test_run_rules_copy_every_limit_and_only_replace_the_symbols(tmp_path):
    d = make_run(tmp_path)
    base, mine = load_rules(RULES).model_dump(), load_rules(d / "rules.yaml").model_dump()
    assert {k: v for k, v in mine.items() if k != "symbols"} == {k: v for k, v in base.items() if k != "symbols"}
    assert mine["symbols"] == {s: {"reference_price": c} for s, c in sorted(OFFLINE_ANCHORS.items())}


def test_a_failing_provider_falls_back_to_offline_and_says_so(tmp_path):
    class Boom:
        def fetch(self, *a):
            raise ProviderError("rate limited (HTTP 429)")
    providers = {"offline": OfflineProvider(), "yfinance": Boom()}
    d = make_run(tmp_path, provider="yfinance", providers=providers)
    man, snap = read_json(d / "manifest.json"), read_json(d / "market_snapshot.json")
    assert man["provider"] == "offline" and man["requested_provider"] == "yfinance"
    assert "rate limited" in man["fallback_reason"] and snap["fallback_reason"] == man["fallback_reason"]
    assert [a["provider"] for a in snap["attempts"]] == ["yfinance", "offline"]


def test_auto_without_alpaca_keys_skips_alpaca_and_uses_yfinance_when_it_works():
    class Fake:
        def fetch(self, symbols, interval):
            from sim.providers import Quotes
            return Quotes("yfinance", {s: 10000 for s in symbols}, interval, "America/New_York")
    f = fetch_with_fallback("auto", ["AAPL"], "1d", providers={"offline": OfflineProvider(), "yfinance": Fake()})
    assert f.quotes.provider == "yfinance" and f.fallback_reason is None
    assert f.attempts[0] == {"provider": "alpaca", "ok": False, "error": "no API key configured"}


def test_a_provider_that_cannot_supply_a_symbol_and_no_anchor_is_a_clear_error(tmp_path):
    class Boom:
        def fetch(self, *a):
            raise ProviderError("offline network")
    with pytest.raises(RunError, match="no price source could supply"):
        make_run(tmp_path, provider="yfinance", symbols=["ZZZZ"], providers={"offline": OfflineProvider(), "yfinance": Boom()})


def test_yfinance_failure_text_does_not_leak_details():
    def dl(symbols, interval):
        raise RuntimeError("https://example/?token=SECRETVALUE")
    with pytest.raises(ProviderError) as e:
        YFinanceProvider(download=dl).fetch(["AAPL"], "1d")
    assert "SECRETVALUE" not in str(e.value) and "RuntimeError" in str(e.value)


def test_yfinance_provider_converts_floats_to_cents_and_rejects_missing():
    ok = YFinanceProvider(download=lambda s, i: ({"AAPL": 190.125, "MSFT": 420.0}, "America/New_York"))
    assert ok.fetch(["AAPL", "MSFT"], "1d").anchors == {"AAPL": 19012, "MSFT": 42000}   # 190.125 -> 19012.5 cents, rounded half-even
    with pytest.raises(ProviderError, match="no price for MSFT"):
        YFinanceProvider(download=lambda s, i: ({"AAPL": 1.0}, "UTC")).fetch(["AAPL", "MSFT"], "1d")


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), "x", 0.004])
def test_to_cents_refuses_unusable_prices(bad):
    with pytest.raises(ProviderError):
        to_cents(bad)


class FakeResp:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self): return self._b


class FakeHttp:
    def __init__(self, resp=None, exc=None): self.resp, self.exc, self.calls = resp, exc, []
    def get(self, url, params=None, headers=None):
        self.calls.append((url, params, headers))
        if self.exc: raise self.exc
        return self.resp


def test_alpaca_reads_latest_trades_only_and_never_places_orders():
    http = FakeHttp(FakeResp(200, {"trades": {"AAPL": {"p": 190.5}, "MSFT": {"p": 421.25}}}))
    q = AlpacaProvider("KEYID", "SECRET", http=http).fetch(["AAPL", "MSFT"], "1d")
    assert q.anchors == {"AAPL": 19050, "MSFT": 42125}
    (url, params, headers), = http.calls
    assert url.startswith("https://data.alpaca.markets/") and "/orders" not in url and "paper-api" not in url
    assert headers["APCA-API-KEY-ID"] == "KEYID"


def test_alpaca_errors_and_artifacts_never_contain_the_keys(tmp_path):
    for http in (FakeHttp(FakeResp(401, {})), FakeHttp(exc=RuntimeError("boom KEYID SECRET")), FakeHttp(FakeResp(200, {"trades": {}}))):
        with pytest.raises(ProviderError) as e:
            AlpacaProvider("KEYID", "SECRET", http=http).fetch(["AAPL"], "1d")
        assert "KEYID" not in str(e.value) and "SECRET" not in str(e.value)
    d = make_run(tmp_path, provider="alpaca", symbols=["AAPL"], env={"ALPACA_KEY_ID": "KEYID", "ALPACA_SECRET_KEY": "SECRET"},
                 providers={"offline": OfflineProvider(), "alpaca": AlpacaProvider("KEYID", "SECRET", http=FakeHttp(FakeResp(403, {})))})
    blob = "".join(p.read_text() for p in d.iterdir() if p.suffix in (".json", ".yaml"))
    assert "KEYID" not in blob and "SECRET" not in blob and "HTTP 403" in blob


def test_dotenv_reader_only_takes_the_two_alpaca_keys(tmp_path):
    f = tmp_path / ".env"
    f.write_text("GEMINI_API_KEY=nope\nALPACA_KEY_ID='abc'\nALPACA_SECRET_KEY=def\n")
    assert alpaca_keys({}, f) == ("abc", "def")
    assert alpaca_keys({"ALPACA_KEY_ID": "e", "ALPACA_SECRET_KEY": "g"}, f) == ("e", "g")   # environment wins


# ---------------------------------------------------------------- runs
def test_same_seed_replays_the_same_run_inputs(tmp_path):
    a, b = make_run(tmp_path, seed=99, profile="impulsive"), make_run(tmp_path, seed=99, profile="impulsive")
    sa, sb = read_json(a / "market_snapshot.json"), read_json(b / "market_snapshot.json")
    assert sa["anchors_minor"] == sb["anchors_minor"]
    assert take(99, "impulsive", 300, sa["anchors_minor"]) == take(99, "impulsive", 300, sb["anchors_minor"])


def test_snapshot_flag_reuses_earlier_prices(tmp_path):
    a = make_run(tmp_path, seed=1)
    b = make_run(tmp_path, seed=1, snapshot_file=a / "market_snapshot.json")
    assert read_json(b / "market_snapshot.json")["anchors_minor"] == read_json(a / "market_snapshot.json")["anchors_minor"]
    assert read_json(b / "market_snapshot.json")["reused_from_file"] is True


@pytest.mark.parametrize("kw", [dict(profile="x"), dict(provider="x"), dict(seed=-1), dict(seed=2 ** 64), dict(seed=True),
                                dict(speed=MAX_SPEED + 1), dict(speed=0), dict(max_orders=0), dict(interval="1s"),
                                dict(symbols=["aapl"]), dict(symbols=["../x"]), dict(symbols=[f"S{i}" for i in range(13)])])
def test_bad_run_settings_are_refused(tmp_path, kw):
    with pytest.raises(RunError):
        make_run(tmp_path, **kw)


@pytest.mark.parametrize("rid", ["..", "../x", "20260101-000000-zzzz", "x/y", "", "20260101-000000-abcd/../..", "20260101-000000-abcde"])
def test_run_ids_are_never_turned_into_arbitrary_paths(tmp_path, rid):
    with pytest.raises(RunError):
        run_dir_for(tmp_path, rid)


# ---------------------------------------------------------------- it only talks to the gate
def test_the_simulator_never_imports_the_log_writer_or_the_gate():
    banned_mods = {"gate", "core.logio", "core.book", "core.ledger", "core.canon"}
    banned_names = {"LogWriter", "event_line", "repair_torn_tail", "Book", "Gate", "event_hash", "canon"}
    for f in sorted((ROOT / "sim").glob("*.py")):
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            mods = [n.module] if isinstance(n, ast.ImportFrom) and n.module else [a.name for a in n.names] if isinstance(n, ast.Import) else []
            for m in mods:
                assert m.split(".")[0] != "gate" and m not in banned_mods, f"{f.name} imports {m}"
            if isinstance(n, ast.ImportFrom):
                assert not ({a.name for a in n.names} & banned_names), f"{f.name} imports a log/gate internal"
            if isinstance(n, ast.Name):
                assert n.id not in banned_names, f"{f.name} uses {n.id}"


def test_a_headless_run_logs_exactly_what_it_sent_through_the_gate(tmp_path, capsys):
    runs = tmp_path / "runs"
    rc = sim_main(["--provider", "offline", "--orders", "150", "--speed", "200", "--seed", "31", "--profile", "impulsive",
                   "--runs-dir", str(runs), "--rules", str(RULES)])
    assert rc == 0 and "SIMULATED" in capsys.readouterr().out
    d, = list(runs.iterdir())
    a = audit(d / "gate.log", load_rules(d / "rules.yaml"))
    assert a.chain_error is None and a.findings == [] and a.events == 150      # the gate wrote it and its own rules agree
    sent = take(31, "impulsive", 150, read_json(d / "market_snapshot.json")["anchors_minor"])
    logged = [json.loads(l) for l in (d / "gate.log").read_text().splitlines()]
    assert [e["client_order_id"] for e in logged] == [i.order["client_order_id"] for i in sent]
    assert [(e["symbol"], e["side"], e["qty"], e["price"]) for e in logged] == [tuple(i.order[k] for k in ("symbol", "side", "qty", "price")) for i in sent]
    st = read_json(d / "sim.status.json")
    assert st["state"] == "done" and st["sent"] == 150 and st["accepted"] + st["rejected"] == 150 and st["errors"] == 0
    assert (d / "gate.out").exists() and not any(p.name.endswith(".torn") for p in d.iterdir())


def test_a_failed_gate_start_is_reported_not_hidden(tmp_path, monkeypatch):
    d = make_run(tmp_path)
    (d / "rules.yaml").write_text("not: [valid")        # the gate refuses to start on bad rules
    rc = sim_main(["--run-dir", str(d), "--quiet"])
    assert rc == 1 and read_json(d / "sim.status.json")["state"] == "failed" and read_json(d / "sim.status.json")["error"]
