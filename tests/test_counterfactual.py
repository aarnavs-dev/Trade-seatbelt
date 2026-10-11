"""Counterfactual replay: hand-computed answers, parity with the live gate, and the declared tie-break."""

import json
import random
from dataclasses import replace
import subprocess
import sys
import tempfile
from pathlib import Path

from hypothesis import given, settings, strategies as st

from auditor.counterfactual import counterfactual
from auditor.fills_csv import Fill, fmt_ts, parse_fills_csv
from core.rules import load_rules
from tests.helpers import run, run_gate, line

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures" / "recon"
EXP = json.loads((FIX / "expected_cf.json").read_text())
RULES = load_rules(ROOT / "rules.yaml")
SEC = 10**9
BASE = 1_760_000_000 * SEC  # a plausible epoch-ns value (2025); the CSV reader rejects other integer scales


def fixture_result():
    return counterfactual(RULES, parse_fills_csv(FIX / "cf_trades.csv").fills)


def test_matches_the_hand_computed_answers():
    r = fixture_result()
    assert (r["orders"], r["accepted"], r["blocked"]) == (EXP["orders"], EXP["accepted"], EXP["blocked"])
    assert r["blocked_by_rule"] == EXP["blocked_by_rule"]
    assert sorted(d["row"] for d in r["decisions"] if d["decision"] == "ACCEPT") == EXP["accepted_rows"]
    assert {str(b["row"]): [b["rule"], b["limit"], b["value"]] for b in r["blocks"]} == EXP["evidence"]
    for k in ("original", "with_rules"):
        got = r["pnl"][k]
        assert {x: got[x] for x in ("gross", "fees", "net", "positions")} == EXP[k]
    assert r["pnl"]["impact"] == EXP["impact"]
    assert r["pnl"]["impact"]["net"] == 5400  # -96590 - (-101990)


def test_a_blocked_trade_changes_later_decisions_so_it_is_not_a_subtraction():
    r = fixture_result()
    dec = {d["row"]: (d["decision"], d["reason_code"]) for d in r["decisions"]}
    # row 18 (SELL 80) closed a long in the original; once row 3 and 5 are blocked it flips the account short,
    # and rows 11 and 13 are blocked only because earlier MSFT trades put the account into cooldown.
    assert dec[18] == ("ACCEPT", "OK") and r["pnl"]["with_rules"]["positions"] == {"AAPL": -50}
    assert dec[11] == ("REJECT", "LOSS_STREAK_COOLDOWN") and dec[13] == ("REJECT", "LOSS_STREAK_COOLDOWN")
    # the AAPL loss (row 18) counts toward the 3-loss streak: without it row 10 would not be in cooldown
    no_aapl_loss = [f for f in parse_fills_csv(FIX / "cf_trades.csv").fills if f.row != 18]
    dec2 = {d["row"]: d["reason_code"] for d in counterfactual(RULES, no_aapl_loss)["decisions"]}
    assert dec2[10] == "OK" and dec2[12] == "LOSS_STREAK_COOLDOWN"  # streak starts at row 7: 1,2,3 at rows 7, 9, 11


def test_replay_order_and_tie_break_are_by_ts_then_row_number():
    fills = parse_fills_csv(FIX / "cf_trades.csv").fills
    # rows 2 and 3 share a timestamp: file order decides, so row 2 (60) is accepted and row 3 (50 -> 110) blocked
    r = counterfactual(RULES, fills)
    assert {d["row"]: d["decision"] for d in r["decisions"]}[2] == "ACCEPT"
    assert [b["row"] for b in r["blocks"]][0] == 3
    # swap only the two rows' numbers: the other one now wins. 50 first -> accepted, then 60 -> 110 blocked.
    swapped = [replace(f, row={2: 3, 3: 2}.get(f.row, f.row)) for f in fills]
    r2 = counterfactual(RULES, swapped)
    assert r2["blocks"][0]["row"] == 3 and r2["blocks"][0]["qty"] == 60  # now the 60-share order is the blocked one


def test_shuffling_rows_with_distinct_timestamps_changes_nothing():
    fills = [f for f in parse_fills_csv(FIX / "cf_trades.csv").fills if f.row != 3]  # drop the tied row
    base = counterfactual(RULES, fills)
    by_row = {f.row: f for f in fills}
    base_dec = {by_row[d["row"]].content(): d["reason_code"] for d in base["decisions"]}
    for seed in range(20):
        sh = list(fills)
        random.Random(seed).shuffle(sh)
        renum = [replace(f, row=i + 2) for i, f in enumerate(sh)]
        r = counterfactual(RULES, renum)
        rr = {f.row: f for f in renum}
        assert {rr[d["row"]].content(): d["reason_code"] for d in r["decisions"]} == base_dec
        assert r["pnl"] == base["pnl"]


def test_a_resend_is_ignored_in_both_runs_like_the_gate_would():
    f = parse_fills_csv(FIX / "cf_trades.csv").fills
    again = f[0]
    r = counterfactual(RULES, f + [replace(again, row=99, ts=again.ts + SEC)])  # same id C01, later
    assert r["duplicates_ignored"] == [{"row": 99, "client_order_id": "C01", "first_seq": 1}]
    assert r["orders"] == 17 and r["pnl"]["impact"] == EXP["impact"]


def test_report_is_deterministic_and_written_by_the_cli(tmp_path):
    outs = []
    for n in "ab":
        o = tmp_path / n
        p = subprocess.run([sys.executable, "-m", "auditor.counterfactual", "--csv", str(FIX / "cf_trades.csv"),
                            "--out", str(o)], cwd=ROOT, capture_output=True, text=True)
        assert p.returncode == 0 and "8 blocked" in p.stdout and "$54.00" in p.stdout
        outs.append(o)
    for name in ("counterfactual.json", "counterfactual.md"):
        assert (outs[0] / name).read_bytes() == (outs[1] / name).read_bytes()


def test_bad_row_fails_with_its_row_number_and_lenient_reports_all(tmp_path):
    lines = (FIX / "cf_trades.csv").read_text().splitlines()
    lines[3] = lines[3].replace(",19300,", ",193.00,")   # row 4
    lines[8] = lines[8].replace("SELL", "LONG")          # row 9 (C08)
    p = tmp_path / "t.csv"
    p.write_text("\n".join(lines) + "\n")
    run_cli = lambda *a: subprocess.run([sys.executable, "-m", "auditor.counterfactual", "--csv", str(p), *a],
                                        cwd=ROOT, capture_output=True, text=True)
    bad = run_cli("--out", str(tmp_path / "o"))
    assert bad.returncode == 4 and "row 4:" in bad.stdout and not (tmp_path / "o").exists()
    ok = run_cli("--lenient", "--out", str(tmp_path / "o"))
    assert ok.returncode == 0
    r = json.loads((tmp_path / "o" / "counterfactual.json").read_text())
    assert [e["row"] for e in r["rejected_rows"]] == [4, 9] and r["orders"] == 15
    assert "NOT replayed" in (tmp_path / "o" / "counterfactual.md").read_text()


# ---- the counterfactual decides exactly like the live gate ------------------------------------------------------

def gate_decisions(fills, rules=RULES):
    """Feed the same orders, in replay order, with the same timestamps, to the real Gate."""
    ordered = sorted(fills, key=lambda f: (f.ts, f.row))
    ids = [f.client_order_id or f"csv-row-{f.row}" for f in ordered]
    clock = iter(f.ts for f in ordered)
    lines = [line(i, f.side, f.symbol, f.qty, f.price) for i, f in zip(ids, ordered)]
    with tempfile.TemporaryDirectory() as d:
        out, gate = run(run_gate(Path(d) / "g.jsonl", lines, rules=rules, clock=lambda: next(clock)))
    return {o["client_order_id"]: (o["decision"], o["reason_code"]) for o in out}, gate


def test_fixture_orders_get_the_same_decisions_from_the_real_gate():
    fills = parse_fills_csv(FIX / "cf_trades.csv").fills
    live, gate = gate_decisions(fills)
    r = counterfactual(RULES, fills)
    by_row = {f.row: f for f in fills}
    mine = {(by_row[d["row"]].client_order_id or f"csv-row-{d['row']}"): (d["decision"], d["reason_code"])
            for d in r["decisions"]}
    assert mine == live and len(live) == 17
    assert sum(1 for v in live.values() if v[0] == "REJECT") == EXP["blocked"]
    # and the gate ended with the same ledger as the counterfactual's "with rules" side
    led = gate.book.ledger
    assert (led.realized_gross, led.fees) == (EXP["with_rules"]["gross"], EXP["with_rules"]["fees"])
    assert {s: q for s, q in led.pos.items() if q} == EXP["with_rules"]["positions"]


def _sym_price(sym, pct):
    ref = {"AAPL": 19000, "MSFT": 42000}.get(sym, 5000)
    return max(1, ref + ref * pct // 100)


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(st.sampled_from(["AAPL", "MSFT", "ZZZ"]), st.sampled_from(["BUY", "SELL"]),
                          st.integers(1, 90), st.integers(-9, 9), st.integers(0, 70)), min_size=1, max_size=40))
def test_property_random_streams_decide_like_the_gate(orders):
    # tight rules so every rule fires: rate 4/min, daily loss 1500, 2-loss cooldown of 120 s, position 100
    rules = RULES.model_copy(update={"max_orders_per_minute": 4, "max_daily_loss": 1500,
                                     "loss_streak_cooldown": RULES.loss_streak_cooldown.model_copy(
                                         update={"losses": 2, "cooldown_seconds": 120})})
    t, fills = BASE, []
    for i, (sym, side, qty, pct, dt) in enumerate(orders):
        t += dt * SEC
        fills.append(Fill(i + 2, t, f"P{i}", sym, side, qty, _sym_price(sym, pct), None))
    live, _ = gate_decisions(fills, rules)
    r = counterfactual(rules, fills)
    assert {f"P{d['row'] - 2}": (d["decision"], d["reason_code"]) for d in r["decisions"]} == live
    for b in r["blocks"]:  # every block carries evidence
        assert b["limit"] is not None and b["value"] is not None and b["row"] >= 2
