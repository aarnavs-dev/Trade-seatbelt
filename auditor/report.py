import orjson

from auditor.replay import Audit
from core.money import fmt

SCOPE = ("Simulated orders only. Verifies the log's hash chain and replays it. It cannot detect "
         "deletion of the newest lines (the chain has no external anchor) and P&L uses the fee "
         "schedule in the rules file passed to the auditor.")


def build_report(a: Audit, log_path: str, recon: dict | None = None) -> dict:
    b, led = a.book, a.book.ledger
    ce = a.chain_error
    r = {
        "log": str(log_path),
        "chain": {"ok": ce is None, "events_verified": a.events,
                  "first_bad_seq": ce.seq if ce else None, "reason": ce.reason if ce else None},
        "partial": ce is not None,
        "counts": {"events": a.events, "accepted": a.accepted,
                   "rejected": a.events - a.accepted, "rejects_by_reason": dict(sorted(a.rejects.items()))},
        "pnl": {"realized_gross": led.realized_gross, "fees": led.fees, "realized_net": b.realized_net,
                "by_symbol": {s: {"gross": g, "fees": f, "net": g - f}
                              for s, (g, f) in sorted(led.by_symbol.items())}},
        "positions": {s: q for s, q in sorted(led.pos.items()) if q},
        "cash": led.cash,
        "cash_identity_ok": led.identity_ok(),
        "findings": a.findings,
        "head_hash": b.last_hash,
        "state_hash": b.state_hash(),
        "scope": SCOPE,
    }
    if recon is not None:  # key is absent when no broker CSV was given, so older reports are unchanged
        r["reconciliation"] = recon
    return r


def to_json(r: dict) -> bytes:
    return orjson.dumps(r, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"


def to_markdown(r: dict) -> str:
    c, p, ch = r["counts"], r["pnl"], r["chain"]
    L = ["# Audit report", ""]
    if ch["ok"]:
        L.append(f"**Chain: OK** ({ch['events_verified']} events verified)")
    else:
        L.append(f"**Chain: BROKEN at seq {ch['first_bad_seq']}**: {ch['reason']}")
        L.append(f"Figures below cover only the {ch['events_verified']} events before the break.")
    L += ["", f"- Events: {c['events']} ({c['accepted']} accepted, {c['rejected']} rejected)"]
    for k, v in c["rejects_by_reason"].items():
        L.append(f"  - {k}: {v}")
    L += ["", "## P&L (FIFO, simulated fills)", "",
          "| Symbol | Gross | Fees | Net |", "|---|---:|---:|---:|"]
    for s, row in p["by_symbol"].items():
        L.append(f"| {s} | {fmt(row['gross'])} | {fmt(row['fees'])} | {fmt(row['net'])} |")
    L.append(f"| **Total** | {fmt(p['realized_gross'])} | {fmt(p['fees'])} | {fmt(p['realized_net'])} |")
    L += ["", f"- Cash: {fmt(r['cash'])} (identity cash = gross - fees - open cost: "
              f"{'holds' if r['cash_identity_ok'] else 'FAILS'})",
          f"- Open positions: {r['positions'] or 'none'}"]
    L += ["", "## Findings", ""]
    L += [f"- seq {f['seq']} {f['kind']}: {f['detail']}" for f in r["findings"]] or ["- none"]
    if "reconciliation" in r:
        L += _recon_md(r["reconciliation"])
    L += ["", f"head_hash `{r['head_hash']}`", f"state_hash `{r['state_hash']}`", "", f"_{r['scope']}_", ""]
    return "\n".join(L)


def _recon_md(rc: dict) -> list[str]:
    L = ["", "## Reconciliation against broker CSV", ""]
    if rc["status"] != "ok":
        return L + [f"**Not run:** {rc['reason']}"]
    L.append(f"{rc['log_fills']} ACCEPTED log fills vs {rc['csv_rows']} broker rows from `{rc['csv']}` "
             f"(tolerance {rc['policy']['tolerance_seconds']}s, inclusive, id-less rows only).")
    if rc["rejected_rows"]:
        L += ["", f"**{len(rc['rejected_rows'])} CSV row(s) were unreadable and are NOT in this comparison "
                  "(lenient mode):**"] + [f"- row {e['row']}: {e['message']}" for e in rc["rejected_rows"]]
    L += ["", "| Category | Count |", "|---|---:|"] + [f"| {k} | {v} |" for k, v in rc["counts"].items()]
    L += ["", "| Category | Log seq | CSV row | Detail |", "|---|---|---|---|"]
    for f in rc["findings"]:
        if f["category"] == "MATCHED":
            continue
        seqs = ", ".join(map(str, f["log_seqs"])) or "-"
        if f.get("related_log_seqs"):
            seqs += f" (see {', '.join(map(str, f['related_log_seqs']))})"
        detail = f['detail']
        if "group" in f:
            detail += f" [group: {f['group']['log_fills']} log fill(s), {f['group']['broker_rows']} broker row(s)]"
        L.append(f"| {f['category']} | {seqs} | {', '.join(map(str, f['csv_rows'])) or '-'} | {detail} |")
    d = rc["implied_difference"]
    pos = ", ".join(f"{s} {q:+d}" for s, q in d["position"].items()) or "none"
    ex = d["excluded_ambiguous"]
    L += ["", f"Implied difference (broker minus log): position {pos}; cash {fmt(d['cash_excl_fees'])} "
              f"(trade notional, fees excluded). Left out: {ex['findings']} AMBIGUOUS finding(s) "
              f"({ex['log_fills']} log fills, {ex['csv_rows']} rows) that cannot be resolved."]
    return L
