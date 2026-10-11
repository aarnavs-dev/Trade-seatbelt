"""Compare the reconciliation and counterfactual reports with the hand-computed expected files, run the same
counterfactual orders through the real Gate, and print a short summary. Exit 1 on any difference.

usage: check_recon_expected.py report.json counterfactual.json fixtures/recon
"""

import asyncio
import json
import os
import sys
import tempfile

import orjson

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auditor.fills_csv import parse_fills_csv  # noqa: E402
from auditor.recon import CATEGORIES  # noqa: E402
from core.money import fmt  # noqa: E402
from core.rules import load_rules  # noqa: E402
from gate.engine import Gate  # noqa: E402

report, cf, fx = (json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), sys.argv[3])
exp_r, exp_c = (json.load(open(os.path.join(fx, n))) for n in ("expected_recon.json", "expected_cf.json"))
rc = report["reconciliation"]
bad = []


def check(name, got, want):
    if got != want:
        bad.append(name)
        print(f"MISMATCH {name}: got {got} expected {want}")


check("recon counts", rc["counts"], exp_r["counts"])
check("recon findings", sorted((f["category"], f["log_seqs"], f["csv_rows"]) for f in rc["findings"]),
      sorted(tuple(x) for x in exp_r["findings"]))
d = rc["implied_difference"]
check("implied position", d["position"], exp_r["implied_position"])
check("implied cash", d["cash_excl_fees"], exp_r["implied_cash_excl_fees"])
check("cf counts", [cf["orders"], cf["accepted"], cf["blocked"]], [exp_c["orders"], exp_c["accepted"], exp_c["blocked"]])
check("cf by rule", cf["blocked_by_rule"], exp_c["blocked_by_rule"])
check("cf evidence", {str(b["row"]): [b["rule"], b["limit"], b["value"]] for b in cf["blocks"]}, exp_c["evidence"])
check("cf impact", cf["pnl"]["impact"], exp_c["impact"])
check("cf original", {k: cf["pnl"]["original"][k] for k in exp_c["original"]}, exp_c["original"])
check("cf with rules", {k: cf["pnl"]["with_rules"][k] for k in exp_c["with_rules"]}, exp_c["with_rules"])

# the same orders, same timestamps, through the real gate
fills = sorted(parse_fills_csv(os.path.join(fx, "cf_trades.csv")).fills, key=lambda f: (f.ts, f.row))
ids = [f.client_order_id or f"csv-row-{f.row}" for f in fills]


async def live():
    clock = iter(f.ts for f in fills)
    with tempfile.TemporaryDirectory() as d_:
        gate = Gate(load_rules(cf["rules"]), os.path.join(d_, "g.jsonl"), "none", clock=lambda: next(clock))
        await gate.start()
        out = []
        for i, f in zip(ids, fills):
            order = orjson.dumps(dict(client_order_id=i, side=f.side, symbol=f.symbol, qty=f.qty, price=f.price)) + b"\n"
            out.append(orjson.loads(await (await gate.submit(order))))
        await gate.stop()
    return {o["client_order_id"]: (o["decision"], o["reason_code"]) for o in out}


live_dec = asyncio.run(live())
rows = {f.row: i for f, i in zip(fills, ids)}
mine = {rows[x["row"]]: (x["decision"], x["reason_code"]) for x in cf["decisions"]}
check("live gate decisions", mine, live_dec)

c = rc["counts"]
pos = ", ".join(f"{s} {q:+d}" for s, q in d["position"].items())
ex = d["excluded_ambiguous"]
yes = lambda prefix: "YES" if not [b for b in bad if b.startswith(prefix)] else "NO  <-- see MISMATCH above"
p = cf["pnl"]
print(f"""RECONCILIATION  gate log vs broker CSV: {rc['log_fills']} ACCEPTED log fills, {rc['csv_rows']} broker rows
  {' | '.join(f'{k} {c[k]}' for k in CATEGORIES)}
  implied difference (broker minus log): {pos} shares; cash {fmt(d['cash_excl_fees'])} (fees excluded);
  {ex['findings']} AMBIGUOUS case left unresolved on purpose
  equals the hand-computed answers: {yes(('recon', 'implied'))}
COUNTERFACTUAL  {cf['orders']} past trades replayed through the gate's rules, in time order
  {cf['blocked']} would have been blocked: {', '.join(f'{k} {v}' for k, v in cf['blocked_by_rule'].items())}
  realized P&L: original {fmt(p['original']['net'])} -> with the rules {fmt(p['with_rules']['net'])}  (impact {'+' if p['impact']['net'] >= 0 else ''}{fmt(p['impact']['net'])})
  equals the hand-computed answers: {yes('cf')}
  same decisions as the live gate on the same {len(live_dec)} orders: {yes('live')}""")
sys.exit(1 if bad else 0)
