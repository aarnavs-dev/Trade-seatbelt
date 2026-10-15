import asyncio
import random

import orjson
from hypothesis import given, settings, strategies as st

from auditor.replay import audit
from core.logio import scan
from fixtures.gen_fixtures import EXPECTED, build
from gate.engine import Gate
from gate.server import start_server
from tests.helpers import RULES, counter_clock, line, run, run_gate


def test_duplicate_returns_original_and_counts_once(tmp_path):
    p = tmp_path / "g.log"
    first = line("D1", "BUY", "AAPL", 10, 19000)
    again = line("D1", "SELL", "AAPL", 99, 1)  # same id, different payload: original wins
    out, gate = run(run_gate(p, [first, again, first]))
    assert [o["duplicate"] for o in out] == [False, True, True]
    assert out[1]["seq"] == out[2]["seq"] == 2 and out[1]["hash"] == out[0]["hash"]  # seq 1 is the gate's rules record
    assert len(list(scan(p))) == 1 and len(p.read_bytes().splitlines()) == 2  # one order line (plus the rules record)
    assert gate.book.ledger.position("AAPL") == 10 and gate.book.ledger.fees == 50


def test_restart_rebuilds_state_and_dedupe(tmp_path):
    p = tmp_path / "g.log"
    _, g1 = run(run_gate(p, [line("A", "BUY", "AAPL", 10, 19000), line("B", "BUY", "AAPL", 5, 19000)]))
    out, g2 = run(run_gate(p, [line("A", "BUY", "AAPL", 10, 19000), line("C", "SELL", "AAPL", 15, 19100)]))
    assert out[0]["duplicate"] and out[1]["seq"] == 4  # 1 = rules record, A = 2, B = 3, C = 4
    assert len(g2.book.rules_records) == 1  # the restart ran the same rules, so it wrote no second record
    g3 = Gate(RULES, p)

    async def boot():
        await g3.start()
        await g3.stop()

    run(boot())
    assert g3.book.state_hash() == g2.book.state_hash()


def test_invalid_orders_are_errors_not_log_lines(tmp_path):
    p = tmp_path / "g.log"
    bad = [b"not json\n", b'{"client_order_id":"x"}\n',
           b'{"client_order_id":"x","side":"BUY","symbol":"AAPL","qty":1.5,"price":19000}\n',
           b'{"client_order_id":"x","side":"BUY","symbol":"AAPL","qty":1,"price":190.0}\n']
    out, _ = run(run_gate(p, bad))
    assert all("error" in o for o in out) and list(scan(p)) == []  # no order line; only the gate's rules record is in the log


def test_fsync_policies_give_identical_logs(tmp_path):
    lines = [l for l in (orjson.dumps(o) + b"\n" for o in build(7))]
    logs = []
    for policy in ("per_order", "batched", "none"):
        p = tmp_path / f"{policy}.log"
        run(run_gate(p, lines, fsync=policy, sequential=(policy == "per_order")))
        logs.append(p.read_bytes())
    assert logs[0] == logs[1] == logs[2]


def test_replay_state_hash_is_byte_identical(tmp_path):
    lines = [orjson.dumps(o) + b"\n" for o in build(7)]
    _, gate = run(run_gate(tmp_path / "g.log", lines))
    hashes = {audit(tmp_path / "g.log", RULES).book.state_hash() for _ in range(3)}
    assert hashes == {gate.book.state_hash()}


def test_fixture_matches_hand_computed_totals(tmp_path):
    lines = [orjson.dumps(o) + b"\n" for o in build(7)]
    out, _ = run(run_gate(tmp_path / "g.log", lines))
    a = audit(tmp_path / "g.log", RULES)
    led = a.book.ledger
    assert a.chain_error is None and a.findings == []
    assert (a.events, a.accepted) == (EXPECTED["events"], EXPECTED["accepted"])
    assert dict(a.rejects) == EXPECTED["rejects_by_reason"]
    assert (led.realized_gross, led.fees, a.book.realized_net, led.cash) == (
        EXPECTED["realized_gross"], EXPECTED["fees"], EXPECTED["realized_net"], EXPECTED["cash"])
    assert {s: q for s, q in led.pos.items() if q} == EXPECTED["positions"]
    assert {s: dict(gross=g, fees=f, net=g - f) for s, (g, f) in led.by_symbol.items()} == EXPECTED["by_symbol"]
    assert sum(o["duplicate"] for o in out) == EXPECTED["duplicates_sent"]


def test_expected_totals_do_not_depend_on_seed(tmp_path):
    for seed in (1, 99):
        p = tmp_path / f"{seed}.log"
        run(run_gate(p, [orjson.dumps(o) + b"\n" for o in build(seed)]))
        a = audit(p, RULES)
        assert (a.book.realized_net, a.book.ledger.cash, a.accepted) == (9150, 9150, 10)


def assert_no_breach(path, rules=RULES):
    """Invariant 1: replay independently; no ACCEPT may leave |position| above the limit
    unless it was reducing, and none may be outside the price band."""
    pos = {}
    for ev in scan(path):
        if ev.decision != "ACCEPT":
            continue
        ref = rules.symbols[ev.symbol].reference_price
        assert abs(ev.price - ref) * 100 <= ref * rules.price_band_pct
        pos[ev.symbol] = pos.get(ev.symbol, 0) + (ev.qty if ev.side == "BUY" else -ev.qty)
        assert abs(pos[ev.symbol]) <= rules.max_position_per_symbol


order_st = st.tuples(st.sampled_from(["AAPL", "MSFT"]), st.sampled_from(["BUY", "SELL"]),
                     st.integers(-5, 160), st.integers(-3000, 3000))


@settings(max_examples=40, deadline=None)
@given(st.lists(order_st, max_size=60))
def test_invariant1_random_streams(tmp_path_factory, stream):
    p = tmp_path_factory.mktemp("h") / "g.log"
    refs = {"AAPL": 19000, "MSFT": 42000}
    lines = [line(f"H{i}", side, sym, qty, refs[sym] + dp) for i, (sym, side, qty, dp) in enumerate(stream)]
    run(run_gate(p, lines, fsync="none"))
    assert_no_breach(p)
    assert audit(p, RULES).findings == []


def test_invariant1_concurrent_clients(tmp_path):
    p = tmp_path / "g.log"

    async def main():
        gate = Gate(RULES, p, "batched", clock=counter_clock())
        await gate.start()
        rng = random.Random(5)

        async def client(c):
            for i in range(60):
                sym = rng.choice(["AAPL", "MSFT"])
                ref = RULES.symbols[sym].reference_price
                fut = await gate.submit(line(f"C{c}-{i}", rng.choice(["BUY", "SELL"]), sym,
                                             rng.randint(1, 60), ref + rng.randint(-1200, 1200)))
                await fut
                await asyncio.sleep(0)

        await asyncio.gather(*(client(c) for c in range(8)))
        await gate.stop()

    run(main())
    assert_no_breach(p)
    evs = list(scan(p))
    assert [e.seq for e in evs] == list(range(2, len(evs) + 2)) and len(evs) == 480  # seq 1 is the rules record
    assert audit(p, RULES).findings == []


def test_tcp_roundtrip_in_order_with_pipelining(tmp_path):
    async def main():
        gate = Gate(RULES, tmp_path / "g.log", "batched", clock=counter_clock())
        await gate.start()
        server = await start_server(gate, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"".join(line(f"T{i}", "BUY", "AAPL", 1, 19000) for i in range(50)) + b"junk\n")
        await w.drain()
        out = [orjson.loads(await r.readline()) for _ in range(51)]
        w.close(); server.close(); await gate.stop()
        return out

    out = run(main())
    assert [o["seq"] for o in out[:50]] == list(range(2, 52)) and "error" in out[50]  # seq 1 is the rules record
