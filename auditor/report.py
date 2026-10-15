import orjson

from auditor.replay import Audit
from core.money import fmt

SCOPE = ("Simulated orders only. Verifies the log's hash chain and replays it. It cannot detect "
         "deletion of the newest lines (the chain has no external anchor). P&L uses the rules file passed to "
         "the auditor; the rules line says whether the log records the rules its orders were decided under.")


def rules_record_summary(a: Audit) -> dict:
    """Whether the log pins the rules its orders were decided under, and whether the rules given to the auditor are those.
    NOT_RECORDED: no rules record at all (a v1 log): the chain is verified but the P&L is only 'under the rules file you gave'.
    PARTIAL: some orders came before the first record, so those are not pinned. MISMATCH: a recorded hash differs from the
    supplied rules (also a RULES_MISMATCH finding). MATCH: every record equals the supplied rules and nothing is unrecorded."""
    recs = a.rules_records
    if not recs:
        status = "NOT_RECORDED"
    elif any(h != a.supplied_rules_hash for _, _, h in recs):
        status = "MISMATCH"
    elif a.unrecorded_events:
        status = "PARTIAL"
    else:
        status = "MATCH"
    return {"status": status, "log_version": 2 if recs else 1, "supplied_rules_hash": a.supplied_rules_hash,
            "recorded": [{"seq": s, "ts": t, "rules_hash": h} for s, t, h in recs],
            "unrecorded_events": a.unrecorded_events}


def build_report(a: Audit, log_path: str, recon: dict | None = None) -> dict:
    b, led = a.book, a.book.ledger
    ce = a.chain_error
    r = {
        "log": str(log_path),
        "chain": {"ok": ce is None, "events_verified": a.events, "records_verified": a.events + len(a.rules_records),
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
        "rules_record": rules_record_summary(a),
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
    L += ["", *_rules_md(r["rules_record"])]
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


def _rules_md(rr: dict) -> list[str]:
    st, sup = rr["status"], rr["supplied_rules_hash"][:12]
    first = rr["recorded"][0]["seq"] if rr["recorded"] else None
    if st == "NOT_RECORDED":
        return [f"**Rules: NOT RECORDED.** This log does not record which rules the gate ran under (a v1 log). The chain is "
                f"verified, but the figures below are only 'what these orders give under the rules file supplied' (hash {sup}); "
                "they are not pinned to what the gate enforced."]
    recorded = ", ".join(f"seq {x['seq']}: {x['rules_hash'][:12]}" for x in rr["recorded"])
    if st == "MISMATCH":
        return [f"**Rules: MISMATCH.** The log records {recorded}; the rules file supplied hashes to {sup}. "
                "The decisions and P&L below were computed with the supplied rules, not the recorded ones (see the RULES_MISMATCH findings)."]
    if st == "PARTIAL":
        return [f"**Rules: PARTIAL.** The rules file supplied ({sup}) matches the log's record ({recorded}), but "
                f"{rr['unrecorded_events']} order(s) before seq {first} come from before rules were recorded and are not pinned."]
    return [f"**Rules: MATCH.** The rules file supplied ({sup}) equals the rules recorded in the log ({recorded})."]


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
