"""Counterfactual: what would the gate have done with these past trades, and what would it have cost or saved?

Method. Rows are sorted by (ts, row number); the row number is the stable tie-break, so two rows with the same
ts are processed in file order. They are then fed one at a time through the gate's own Book.verdict() (the pure
check() plus the state it reads) and, if accepted, Book.apply(). Because a blocked trade is not applied, every
later decision sees the state *without* it. This is why the result is a sequential replay and not the original
P&L minus the blocked trades: removing one trade changes positions, realized P&L, the loss streak and the order
window that the later rules read, so later trades can flip from accepted to blocked or the reverse. The
`ts` handed to the Book is max(row ts, previous ts), the same rule the gate applies to its own clock.

Assumptions (also in docs/LIMITATIONS.md):
 - Every accepted order fills fully, immediately, at its CSV price. No partial fills, no slippage, no queue.
 - Original P&L is the same FIFO ledger run over every row (no rules), with fees from rules.yaml for BOTH sides of the
   comparison; the CSV fee column is ignored here so the two numbers are comparable.
 - A repeated client_order_id is a resend: like the gate, it is ignored (not counted as an order), in both runs.
   A row without an id gets a synthetic id from its row number.
 - P&L is realized only. Positions still open at the end are reported, not marked to market, and can differ between
   the two runs.
"""

import argparse
import os
import re

from auditor.fills_csv import CsvError, Fill, fmt_ts, parse_fills_csv
from auditor.report import to_json
from core.book import WINDOW_NS, Book
from core.events import Order
from core.ledger import Ledger
from core.money import fmt
from core.rules import Rules, load_rules

ASSUMPTIONS = [
    "every accepted order fills fully, immediately, at its CSV price (no partial fills, slippage or queue)",
    "original P&L = FIFO ledger over every row with fees from rules.yaml; the CSV fee column is ignored",
    "replay is sequential in (ts, row number) order using the gate's own Book and check(); not a subtraction",
    "a repeated client_order_id is a resend and is ignored in both runs, as the gate would",
    "realized P&L only; open positions are listed but not marked to market",
]
_SYNTHETIC = re.compile(r"^csv-row-\d+$")


def _evidence(rules: Rules, book: Book, o: Order, ts: int, code: str) -> tuple:
    """(limit, value) for a block, from the same state check() read. Strings where a number would mislead."""
    pos = book.ledger.position(o.symbol)
    if code == "BAD_ORDER":
        return "symbol in rules.yaml", o.symbol
    if code == "KILL_SWITCH":
        return "kill_switch: false", "kill_switch: true"
    if code == "RATE_LIMIT":
        n = sum(1 for t in book.order_times if t > ts - WINDOW_NS)
        return rules.max_orders_per_minute, n
    if code == "PRICE_BAND":
        ref = rules.symbols[o.symbol].reference_price
        return f"{rules.price_band_pct}% of reference {ref}", f"{o.price} (deviation {abs(o.price - ref)})"
    if code == "MAX_POSITION":
        return rules.max_position_per_symbol, pos + (o.qty if o.side == "BUY" else -o.qty)
    if code == "MAX_DAILY_LOSS":
        return rules.max_daily_loss, -book.realized_net
    if code == "LOSS_STREAK_COOLDOWN":
        return f"cooldown until {fmt_ts(book.cooldown_until)}", f"order at {fmt_ts(ts)}"
    raise AssertionError(f"no evidence rule for {code}")


def _pnl(led: Ledger) -> dict:
    return {"gross": led.realized_gross, "fees": led.fees, "net": led.realized_gross - led.fees,
            "by_symbol": {s: {"gross": g, "fees": f, "net": g - f} for s, (g, f) in sorted(led.by_symbol.items())},
            "positions": {s: q for s, q in sorted(led.pos.items()) if q}}


def counterfactual(rules: Rules, fills: list[Fill]) -> dict:
    for f in fills:
        if f.client_order_id and _SYNTHETIC.match(f.client_order_id):
            raise CsvError(f.row, f"client_order_id '{f.client_order_id}' collides with the synthetic ids "
                                  "(csv-row-N) given to rows that have none")
    book, original = Book(rules), Ledger()
    decisions, blocks, dups = [], [], []
    for f in sorted(fills, key=lambda f: (f.ts, f.row)):
        oid = f.client_order_id or f"csv-row-{f.row}"
        if oid in book.seen:  # a resend: the gate returns the original decision and counts nothing
            dups.append({"row": f.row, "client_order_id": oid, "first_seq": book.seen[oid].seq})
            continue
        o = Order(client_order_id=oid, symbol=f.symbol, side=f.side, qty=f.qty, price=f.price)
        ts = max(f.ts, book.last_ts)
        v = book.verdict(o, ts)
        limit = value = None
        if not v.accept:
            limit, value = _evidence(rules, book, o, ts, v.code)
        book.apply(book.make_event(o, ts, v))
        original.fill(f.symbol, f.side, f.qty, f.price, rules.fees.of(f.qty))
        decisions.append({"row": f.row, "decision": "ACCEPT" if v.accept else "REJECT", "reason_code": v.code})
        if not v.accept:
            blocks.append({"row": f.row, "ts": fmt_ts(f.ts), "client_order_id": f.client_order_id,
                           "symbol": f.symbol, "side": f.side, "qty": f.qty, "price": f.price,
                           "rule": v.code, "limit": limit, "value": value, "gate_text": v.text})
    by_rule: dict[str, int] = {}
    for b in blocks:
        by_rule[b["rule"]] = by_rule.get(b["rule"], 0) + 1
    orig, cf = _pnl(original), _pnl(book.ledger)
    return {
        "orders": len(decisions), "accepted": len(decisions) - len(blocks), "blocked": len(blocks),
        "duplicates_ignored": dups, "blocked_by_rule": dict(sorted(by_rule.items())),
        "blocks": blocks, "decisions": decisions,
        "pnl": {"original": orig, "with_rules": cf,
                "impact": {k: cf[k] - orig[k] for k in ("gross", "fees", "net")}},
        "replay_order": "sorted by (ts, CSV row number); row number is the stable tie-break",
        "assumptions": ASSUMPTIONS,
    }


def to_markdown(r: dict) -> str:
    p, i = r["pnl"], r["pnl"]["impact"]
    L = ["# Counterfactual replay", "",
         f"Source `{r['csv']}` replayed through the gate's rules in (ts, row) order.", ""]
    if r["rejected_rows"]:
        L += [f"**{len(r['rejected_rows'])} CSV row(s) were unreadable and are NOT replayed (lenient mode):**"]
        L += [f"- row {e['row']}: {e['message']}" for e in r["rejected_rows"]] + [""]
    rules_txt = ", ".join(f"{k} {v}" for k, v in r["blocked_by_rule"].items()) or "none"
    L += [f"- Trades replayed: {r['orders']} ({r['accepted']} would be accepted, **{r['blocked']} blocked**: {rules_txt})",
          f"- Resends ignored: {len(r['duplicates_ignored'])}", "",
          "| | Gross | Fees | Net |", "|---|---:|---:|---:|",
          *[f"| {name} | {fmt(x['gross'])} | {fmt(x['fees'])} | {fmt(x['net'])} |"
            for name, x in (("Original trades", p["original"]), ("Trades the rules would accept", p["with_rules"]))],
          f"| **Impact (with rules minus original)** | {fmt(i['gross'])} | {fmt(i['fees'])} | **{fmt(i['net'])}** |", "",
          f"Open positions at the end: original {p['original']['positions'] or 'none'}, "
          f"with rules {p['with_rules']['positions'] or 'none'} (not marked to market).", "",
          "## Blocked trades", "", "| CSV row | Rule | Limit | Value | Trade |", "|---:|---|---|---|---|"]
    L += [f"| {b['row']} | {b['rule']} | {b['limit']} | {b['value']} | {b['side']} {b['qty']} {b['symbol']} "
          f"@ {fmt(b['price'])} at {b['ts']} |" for b in r["blocks"]] or ["| - | - | - | - | none |"]
    L += ["", "## Assumptions", ""] + [f"- {a}" for a in r["assumptions"]] + [""]
    return "\n".join(L)


def run(csv_path, rules_path, lenient: bool = False) -> dict:
    parsed = parse_fills_csv(csv_path, lenient=lenient)
    r = counterfactual(load_rules(rules_path), parsed.fills)
    r["csv"], r["rules"] = str(csv_path), str(rules_path)
    r["rejected_rows"] = [{"row": e.row, "message": e.message} for e in parsed.errors]
    return r


if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="auditor.counterfactual")
    p.add_argument("--csv", required=True, help="past trades, same format as the broker fills CSV")
    p.add_argument("--rules", default="rules.yaml")
    p.add_argument("--out", help="directory for counterfactual.json and counterfactual.md")
    p.add_argument("--lenient", action="store_true", help="skip and report every bad row instead of failing on the first")
    args = p.parse_args()
    try:
        r = run(args.csv, args.rules, args.lenient)
    except CsvError as e:
        print(f"TRADES CSV REJECTED, nothing written: {e}")
        raise SystemExit(4)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "counterfactual.json"), "wb") as f:
            f.write(to_json(r))
        with open(os.path.join(args.out, "counterfactual.md"), "w") as f:
            f.write(to_markdown(r))
    print(f"{r['orders']} trades, {r['blocked']} blocked {r['blocked_by_rule']}, "
          f"net impact {fmt(r['pnl']['impact']['net'])}")
