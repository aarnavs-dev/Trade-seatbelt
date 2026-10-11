"""Reconciliation: hand-computed fixture answers, determinism, shuffle-invariance, and no guessing."""

import json
import random
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from hypothesis import HealthCheck, given, settings, strategies as st

from auditor.fills_csv import Fill, parse_fills_csv
from auditor.recon import CATEGORIES, reconcile
from auditor.replay import audit
from core.rules import load_rules

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures" / "recon"
EXPECTED = json.loads((FIX / "expected_recon.json").read_text())
SEC = 10**9
RULES = load_rules(ROOT / "rules.yaml")


def run_recon(log, csv, tol=5):
    a = audit(log, RULES)
    assert a.chain_error is None
    parsed = parse_fills_csv(csv)
    return reconcile(a.fills, parsed.fills, tol, a.rejected), {f.row: f for f in parsed.fills}


def canonical(rc, rows):
    """The result with row numbers replaced by row content, so files that differ only in row order compare equal."""
    return ([(f["category"], f["log_seqs"], f.get("related_log_seqs", []),
              sorted(rows[n].content() for n in f["csv_rows"]), f["detail"], f.get("differs", []))
             for f in rc["findings"]], rc["counts"], rc["implied_difference"])


def test_fixture_matches_hand_computed_answers():
    rc, _ = run_recon(FIX / "gate_log.jsonl", FIX / "broker_fills.csv")
    assert rc["log_fills"] == EXPECTED["log_fills"] and rc["csv_rows"] == EXPECTED["csv_rows"]
    assert rc["counts"] == EXPECTED["counts"]
    got = sorted((f["category"], f["log_seqs"], f["csv_rows"]) for f in rc["findings"])
    assert got == sorted(tuple(x) for x in EXPECTED["findings"])
    d = rc["implied_difference"]
    assert d["position"] == EXPECTED["implied_position"] and d["cash_excl_fees"] == EXPECTED["implied_cash_excl_fees"]
    assert d["excluded_ambiguous"] == EXPECTED["excluded_ambiguous"]


def test_every_log_fill_and_every_row_is_accounted_for_exactly_once():
    rc, rows = run_recon(FIX / "gate_log.jsonl", FIX / "broker_fills.csv")
    seqs = [s for f in rc["findings"] for s in f["log_seqs"]]
    nums = [n for f in rc["findings"] for n in f["csv_rows"]]
    assert sorted(seqs) == [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13]  # the REJECT at seq 6 is not a fill
    assert sorted(nums) == sorted(rows)


def test_rejected_order_that_the_broker_filled_cites_the_reject():
    rc, _ = run_recon(FIX / "gate_log.jsonl", FIX / "broker_fills.csv")
    (f,) = [f for f in rc["findings"] if f["csv_rows"] == [15]]
    assert f["category"] == "MISSING_IN_LOG" and f["related_log_seqs"] == [6] and "PRICE_BAND" in f["detail"]


def test_tolerance_is_inclusive_and_declared():
    rc, _ = run_recon(FIX / "gate_log.jsonl", FIX / "broker_fills.csv", tol=5)
    assert ("MATCHED", [10], [11]) in [(f["category"], f["log_seqs"], f["csv_rows"]) for f in rc["findings"]]  # exactly 5s
    rc, _ = run_recon(FIX / "gate_log.jsonl", FIX / "broker_fills.csv", tol=4)
    got = [(f["category"], f["log_seqs"], f["csv_rows"]) for f in rc["findings"]]
    assert ("MATCHED", [10], [11]) not in got and ("MISSING_IN_BROKER", [10], []) in got
    rc, _ = run_recon(FIX / "gate_log.jsonl", FIX / "broker_fills.csv", tol=6)  # 6s now reaches seq 13's row 14
    assert ("MATCHED", [13], [14]) in [(f["category"], f["log_seqs"], f["csv_rows"]) for f in rc["findings"]]
    assert rc["policy"]["tolerance_seconds"] == 6 and rc["policy"]["tolerance_inclusive"] is True


def _cli(tmp_path, name, log, csv, *extra):
    out = tmp_path / name
    p = subprocess.run([sys.executable, "-m", "auditor", "--log", str(log), "--fills-csv", str(csv), "--out", str(out),
                        "--rules", str(ROOT / "rules.yaml"), *extra], cwd=ROOT, capture_output=True, text=True)
    return p, out


def test_reconciliation_is_deterministic_byte_for_byte(tmp_path):
    (pa, a), (pb, b) = (_cli(tmp_path, n, FIX / "gate_log.jsonl", FIX / "broker_fills.csv") for n in "ab")
    assert pa.returncode == pb.returncode == 3  # findings exist
    for name in ("report.json", "report.md"):
        assert (a / name).read_bytes() == (b / name).read_bytes()
    assert json.loads((a / "report.json").read_text())["reconciliation"]["counts"] == EXPECTED["counts"]


def test_shuffling_csv_rows_does_not_change_the_result(tmp_path):
    base_rc, base_rows = run_recon(FIX / "gate_log.jsonl", FIX / "broker_fills.csv")
    want = canonical(base_rc, base_rows)
    head, *body = (FIX / "broker_fills.csv").read_text().splitlines()
    for seed in range(30):
        random.Random(seed).shuffle(body)
        p = tmp_path / f"s{seed}.csv"
        p.write_text("\n".join([head, *body]) + "\n")
        rc, rows = run_recon(FIX / "gate_log.jsonl", p)
        assert canonical(rc, rows) == want, f"seed {seed}"
        assert rc["counts"] == EXPECTED["counts"]


def test_json_report_is_identical_after_shuffle_except_cited_row_numbers(tmp_path):
    # strongest form on bytes: sort the rows into the original order and the reports are byte-identical
    head, *body = (FIX / "broker_fills.csv").read_text().splitlines()
    shuffled = list(body)
    random.Random(1).shuffle(shuffled)
    p = tmp_path / "shuf.csv"
    p.write_text("\n".join([head, *shuffled]) + "\n")
    q = tmp_path / "orig.csv"
    q.write_text("\n".join([head, *body]) + "\n")
    _, a = _cli(tmp_path, "a", FIX / "gate_log.jsonl", q)
    _, b = _cli(tmp_path, "b", FIX / "gate_log.jsonl", q)
    assert (a / "report.json").read_bytes() == (b / "report.json").read_bytes()
    _, c = _cli(tmp_path, "c", FIX / "gate_log.jsonl", p)
    ra, rc_ = (json.loads((x / "report.json").read_text())["reconciliation"] for x in (a, c))
    assert ra["counts"] == rc_["counts"] and ra["implied_difference"] == rc_["implied_difference"]
    assert [(f["category"], f["log_seqs"]) for f in ra["findings"]] == [(f["category"], f["log_seqs"]) for f in rc_["findings"]]


# ---- ambiguity is never resolved -------------------------------------------------------------------------------

def ev(seq, ts_s, sym="AAPL", side="BUY", qty=10, price=19000, oid=None):
    return SimpleNamespace(seq=seq, ts=ts_s * SEC, client_order_id=oid or f"L{seq}", symbol=sym, side=side,
                           qty=qty, price=price)


def row(n, ts_s, sym="AAPL", side="BUY", qty=10, price=19000, oid=None, fee=None):
    return Fill(n, ts_s * SEC, oid, sym, side, qty, price, fee)


def cats(rc):
    return [(f["category"], f["log_seqs"], f["csv_rows"]) for f in rc["findings"]]


def test_two_identical_fills_and_two_identical_rows_are_ambiguous_not_paired():
    rc = reconcile([ev(1, 0), ev(2, 1)], [row(2, 0), row(3, 1)])
    assert cats(rc) == [("AMBIGUOUS", [1, 2], [2, 3])]
    assert rc["counts"]["MATCHED"] == 0 and rc["implied_difference"]["position"] == {}


def test_one_row_two_fills_and_two_rows_one_fill_are_ambiguous():
    assert cats(reconcile([ev(1, 0), ev(2, 2)], [row(2, 1)])) == [("AMBIGUOUS", [1, 2], [2])]
    assert cats(reconcile([ev(1, 1)], [row(2, 0), row(3, 2)])) == [("AMBIGUOUS", [1], [2, 3])]


def test_a_chain_of_candidates_is_one_ambiguous_component_even_if_a_greedy_pairing_exists():
    # fill@0, row@4, fill@8, row@12 with tol 5: row@4 fits both fills, fill@8 fits both rows.
    # Greedy pairing (0-4, 8-12) would "work"; the policy refuses to choose.
    rc = reconcile([ev(1, 0), ev(2, 8)], [row(2, 4), row(3, 12)])
    assert cats(rc) == [("AMBIGUOUS", [1, 2], [2, 3])]


def test_unambiguous_neighbours_are_still_matched():
    rc = reconcile([ev(1, 0, qty=5), ev(2, 1, qty=7)], [row(2, 0, qty=7), row(3, 1, qty=5)])
    assert sorted(cats(rc)) == [("MATCHED", [1], [3]), ("MATCHED", [2], [2])]


def test_ambiguous_findings_are_excluded_from_the_implied_difference_but_counted():
    rc = reconcile([ev(1, 0), ev(2, 1)], [row(2, 0)])
    assert rc["implied_difference"]["excluded_ambiguous"] == {"findings": 1, "log_fills": 2, "csv_rows": 1}
    assert rc["implied_difference"]["cash_excl_fees"] == 0


def test_an_id_on_the_row_is_never_re_matched_on_fields():
    # row carries an id the log does not know, but its fields fit seq 1 exactly: still MISSING on both sides
    rc = reconcile([ev(1, 0)], [row(2, 0, oid="NOPE")])
    assert sorted(cats(rc)) == [("MISSING_IN_BROKER", [1], []), ("MISSING_IN_LOG", [], [2])]


def test_same_id_but_different_symbol_is_not_a_match_and_not_a_mismatch():
    rc = reconcile([ev(1, 0, oid="X")], [row(2, 0, sym="MSFT", oid="X")])
    assert sorted(cats(rc)) == [("MISSING_IN_BROKER", [1], []), ("MISSING_IN_LOG", [], [2])]
    # hand: log BUY 10 AAPL @19000 removed (AAPL -10, cash +190000); broker BUY 10 MSFT added (MSFT +10, cash -190000)
    d = rc["implied_difference"]
    assert d["position"] == {"AAPL": -10, "MSFT": 10} and d["cash_excl_fees"] == 0


def test_same_id_rows_that_disagree_with_each_other_are_ambiguous():
    rc = reconcile([ev(1, 0, oid="X")], [row(2, 0, oid="X"), row(3, 0, qty=9, oid="X")])
    assert cats(rc) == [("AMBIGUOUS", [1], [2, 3])]


def test_qty_and_price_both_wrong_is_one_finding_listing_both():
    rc = reconcile([ev(1, 0, oid="X")], [row(2, 0, qty=9, price=19100, oid="X")])
    (f,) = rc["findings"]
    assert f["category"] == "QTY_MISMATCH" and f["differs"] == ["qty", "price"]
    # hand: broker BUY 9@19100 = pos +9 cash -171900; log BUY 10@19000 = pos +10 cash -190000 -> pos -1, cash +18100
    assert rc["implied_difference"]["position"] == {"AAPL": -1} and rc["implied_difference"]["cash_excl_fees"] == 18100


@st.composite
def scenario(draw):
    keys = st.tuples(st.sampled_from(["AAPL", "MSFT"]), st.sampled_from(["BUY", "SELL"]),
                     st.sampled_from([1, 2]), st.sampled_from([100, 101]))
    logs = [ev(i + 1, t, *k) for i, (k, t) in enumerate(draw(st.lists(st.tuples(keys, st.integers(0, 20)), max_size=7)))]
    rows = [row(i + 2, t, *k) for i, (k, t) in enumerate(draw(st.lists(st.tuples(keys, st.integers(0, 20)), max_size=7)))]
    return logs, rows


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(scenario(), st.integers(0, 6))
def test_property_pairs_only_when_the_candidate_is_unique_on_both_sides(sc, tol):
    logs, rows = sc
    rc = reconcile(logs, rows, tol)
    key = lambda x: (x.symbol, x.side, x.qty, x.price)
    cand = lambda e, r: key(e) == key(r) and abs(e.ts - r.ts) <= tol * SEC
    deg_l = {e.seq: sum(cand(e, r) for r in rows) for e in logs}
    deg_r = {r.row: sum(cand(e, r) for e in logs) for r in rows}
    by_item = {}
    for f in rc["findings"]:
        for s in f["log_seqs"]:
            assert ("L", s) not in by_item  # each item in exactly one finding
            by_item[("L", s)] = f
        for n in f["csv_rows"]:
            assert ("R", n) not in by_item
            by_item[("R", n)] = f
    assert len(by_item) == len(logs) + len(rows)
    for e in logs:
        f = by_item[("L", e.seq)]
        if deg_l[e.seq] == 0:
            assert f["category"] == "MISSING_IN_BROKER"
        elif f["category"] == "MATCHED":
            (n,) = f["csv_rows"]
            assert deg_l[e.seq] == 1 and deg_r[n] == 1 and cand(e, next(r for r in rows if r.row == n))
        else:
            assert f["category"] == "AMBIGUOUS"
    for r in rows:
        if deg_r[r.row] == 0:
            assert by_item[("R", r.row)]["category"] == "MISSING_IN_LOG"
        if deg_r[r.row] >= 2:
            assert by_item[("R", r.row)]["category"] == "AMBIGUOUS"
    assert sum(rc["counts"].values()) == len(rc["findings"]) and set(rc["counts"]) == set(CATEGORIES)


@settings(max_examples=150, deadline=None)
@given(scenario(), st.randoms(use_true_random=False))
def test_property_row_order_never_changes_the_result(sc, rnd):
    logs, rows = sc
    shuffled = list(rows)
    rnd.shuffle(shuffled)
    renumbered = [Fill(i + 2, r.ts, r.client_order_id, r.symbol, r.side, r.qty, r.price, r.fee) for i, r in enumerate(shuffled)]
    a, b = reconcile(logs, rows, 3), reconcile(logs, renumbered, 3)
    ra, rb = {r.row: r for r in rows}, {r.row: r for r in renumbered}
    assert canonical(a, ra) == canonical(b, rb)


# ---- CLI behaviour ----------------------------------------------------------------------------------------------

def test_malformed_broker_csv_fails_the_whole_run_and_names_the_row(tmp_path):
    bad = tmp_path / "bad.csv"
    lines = (FIX / "broker_fills.csv").read_text().splitlines()
    lines[6] = lines[6].replace(",42100,", ",421.00,")  # file line 7 = row 7 (L005)
    bad.write_text("\n".join(lines) + "\n")
    p, out = _cli(tmp_path, "o", FIX / "gate_log.jsonl", bad)
    assert p.returncode == 4 and "row 7:" in p.stdout and "floats as money" in p.stdout
    assert not out.exists()  # nothing half-written


def test_lenient_run_reports_the_bad_rows_and_reconciles_the_rest(tmp_path):
    bad = tmp_path / "bad.csv"
    lines = (FIX / "broker_fills.csv").read_text().splitlines()
    lines[6] = lines[6].replace(",42100,", ",421.00,")  # row 7 (L005) becomes unreadable
    lines[9] = lines[9].replace("BUY", "buy")           # row 10 (L009) too
    bad.write_text("\n".join(lines) + "\n")
    p, out = _cli(tmp_path, "o", FIX / "gate_log.jsonl", bad, "--lenient")
    assert p.returncode == 3
    rc = json.loads((out / "report.json").read_text())["reconciliation"]
    assert [e["row"] for e in rc["rejected_rows"]] == [7, 10]
    assert rc["csv_rows"] == 12
    md = (out / "report.md").read_text()
    assert "row 7:" in md and "row 10:" in md and "NOT in this comparison" in md
    # hand: seq 9's row is unreadable, so seq 9 now looks missing (with seq 7, 13 = 3); L005's duplicate row 8 becomes
    # its only row and matches. That distortion is exactly what the "NOT in this comparison" warning is for.
    assert rc["counts"]["MISSING_IN_BROKER"] == 3 and rc["counts"]["MATCHED"] == 6 and rc["counts"]["PRICE_MISMATCH"] == 0


def test_broken_chain_means_no_reconciliation_is_attempted(tmp_path):
    log = tmp_path / "t.jsonl"
    lines = (FIX / "gate_log.jsonl").read_text().splitlines(keepends=True)
    del lines[4]
    log.write_text("".join(lines))
    p, out = _cli(tmp_path, "o", log, FIX / "broker_fills.csv")
    assert p.returncode == 2
    rc = json.loads((out / "report.json").read_text())["reconciliation"]
    assert rc["status"] == "not_run" and "seq 5" in rc["reason"] and "findings" not in rc
    assert "Not run" in (out / "report.md").read_text()


def test_report_without_a_csv_is_unchanged(tmp_path):
    out = tmp_path / "o"
    subprocess.run([sys.executable, "-m", "auditor", "--log", str(FIX / "gate_log.jsonl"), "--out", str(out)],
                   cwd=ROOT, capture_output=True, check=True)
    assert "reconciliation" not in json.loads((out / "report.json").read_text())


def test_committed_fixtures_are_what_the_generator_produces(tmp_path):
    out = tmp_path / "regen"
    subprocess.run([sys.executable, "fixtures/gen_recon_fixtures.py", "--out", str(out)], cwd=ROOT, check=True,
                   capture_output=True)
    for f in FIX.iterdir():
        assert (out / f.name).read_bytes() == f.read_bytes(), f.name
    assert sorted(p.name for p in out.iterdir()) == sorted(p.name for p in FIX.iterdir())
    shutil.rmtree(out)
