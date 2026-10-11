"""Reconcile the gate log's ACCEPTED fills with a broker fills CSV. Nothing is guessed.

Matching policy (declared, deterministic, independent of CSV row order):

 1. By client_order_id, when the CSV row has one. An id on a row is authoritative: it is never re-matched on fields.
    - one log fill, one row, same symbol+side: MATCHED, or QTY_MISMATCH / PRICE_MISMATCH if they differ.
      qty wins when both differ (the finding lists every differing field in `differs`).
    - one log fill, several rows with that id: if the rows agree on symbol/side/qty/price they are one broker fill
      reported more than once. The earliest by (ts, content, row) pairs with the log fill; each extra copy is
      MISSING_IN_LOG ("duplicate"). If the rows disagree with each other: AMBIGUOUS.
    - several log fills with that id (cannot come from the gate): AMBIGUOUS.
    - same id but different symbol or side: not the same trade; the log fill is MISSING_IN_BROKER and the row
      MISSING_IN_LOG, each saying so.
    - row id has no ACCEPT in the log: MISSING_IN_LOG (the detail names the REJECT if the log has one).
 2. Rows without an id vs log fills not used by step 1, on exactly (symbol, side, qty, price) with
    |broker ts - log ts| <= tolerance (inclusive). Candidate pairs form a graph; each connected component is
    resolved as a whole: exactly one fill and one row -> MATCHED; anything bigger -> AMBIGUOUS (all members cited,
    none paired). Unconnected items are MISSING_IN_BROKER / MISSING_IN_LOG. Because components do not depend on
    visiting order, shuffling the CSV cannot change a result. Without an id, a qty or price difference cannot be
    told from two different trades, so those show up as one MISSING_IN_BROKER plus one MISSING_IN_LOG.

Findings are ordered by (first log seq, first row ts, first row content, category); row number is the last tie-break,
used only between byte-identical rows. Details never contain row numbers, so they are shuffle-invariant too.

Implied difference = broker minus log, summed over MISSING_*, QTY_MISMATCH and PRICE_MISMATCH findings. Cash is
trade notional only (BUY pays qty*price, SELL receives it); fees are excluded because the log does not record them
and the CSV fee is optional. AMBIGUOUS findings are excluded and counted.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from auditor.fills_csv import Fill

CATEGORIES = ("MATCHED", "MISSING_IN_BROKER", "MISSING_IN_LOG", "QTY_MISMATCH", "PRICE_MISMATCH", "AMBIGUOUS")
DEFAULT_TOLERANCE_S = 5
_BIG = 1 << 62


@dataclass
class Finding:
    category: str
    detail: str
    logs: list = field(default_factory=list)      # ACCEPT events this finding is about
    rows: list = field(default_factory=list)      # Fill rows this finding is about
    related: list = field(default_factory=list)   # other log seqs worth citing (a REJECT, the paired fill)
    differs: list = field(default_factory=list)

    def sort_key(self):
        rows = sorted(self.rows, key=_row_key)
        return (min((e.seq for e in self.logs), default=_BIG),
                _row_key(rows[0])[:-1] if rows else (_BIG,), self.category,
                min(self.related, default=0), rows[0].row if rows else 0)

    def as_dict(self) -> dict:
        d = {"category": self.category, "log_seqs": sorted(e.seq for e in self.logs),
             "csv_rows": sorted(r.row for r in self.rows), "detail": self.detail}
        if self.related:
            d["related_log_seqs"] = sorted(self.related)
        if self.differs:
            d["differs"] = self.differs
        return d


def _row_key(r: Fill):
    return (*r.content(), r.row)


def _eff(side: str, qty: int, price: int) -> tuple[int, int]:
    signed = qty if side == "BUY" else -qty
    return signed, -signed * price


def _implied(findings) -> dict:
    pos: dict[str, int] = defaultdict(int)
    cash, amb = 0, [0, 0, 0]
    for f in findings:
        if f.category == "AMBIGUOUS":
            amb[0] += 1
            amb[1] += len(f.logs)
            amb[2] += len(f.rows)
        if f.category in ("MATCHED", "AMBIGUOUS"):
            continue
        for sign, items in ((1, f.rows), (-1, f.logs)):  # broker minus log
            for it in items:
                p, c = _eff(it.side, it.qty, it.price)
                pos[it.symbol] += sign * p
                cash += sign * c
    return {
        "convention": "broker minus log; log figures plus this = broker-implied figures, for the findings counted here",
        "position": {s: q for s, q in sorted(pos.items()) if q},
        "cash_excl_fees": cash,
        "excluded_ambiguous": {"findings": amb[0], "log_fills": amb[1], "csv_rows": amb[2]},
    }


def _compare(log, row) -> Finding:
    diff = [n for n in ("qty", "price") if getattr(log, n) != getattr(row, n)]
    if not diff:
        return Finding("MATCHED", f"same id, symbol, side, qty {log.qty}, price {log.price}", [log], [row])
    cat = "QTY_MISMATCH" if "qty" in diff else "PRICE_MISMATCH"
    what = "; ".join(f"{n} log {getattr(log, n)} vs broker {getattr(row, n)}" for n in diff)
    return Finding(cat, f"same id, {what}", [log], [row], differs=diff)


def _by_id(log_fills, rows, rejected):
    out: list[Finding] = []
    used_logs: set[int] = set()
    used_rows: set[int] = set()
    logs_by_id, rows_by_id = defaultdict(list), defaultdict(list)
    for e in log_fills:
        logs_by_id[e.client_order_id].append(e)
    for r in rows:
        if r.client_order_id is not None:
            rows_by_id[r.client_order_id].append(r)
    for oid, rs in rows_by_id.items():
        rs.sort(key=_row_key)
        ls = logs_by_id.get(oid, [])
        used_rows.update(r.row for r in rs)
        if not ls:
            hint = rejected.get(oid)
            for r in rs:
                out.append(Finding(
                    "MISSING_IN_LOG",
                    "broker has a fill the gate did not accept; the log REJECTED this id (" + hint[1] + ")"
                    if hint else "broker fill whose client_order_id has no ACCEPT in the log",
                    rows=[r], related=[hint[0]] if hint else []))
            continue
        used_logs.update(e.seq for e in ls)
        if len(ls) > 1:
            out.append(Finding("AMBIGUOUS", f"{len(ls)} ACCEPTED log fills share this client_order_id", ls, rs))
            continue
        log = ls[0]
        same = [r for r in rs if (r.symbol, r.side) == (log.symbol, log.side)]
        for r in rs:
            if r not in same:
                out.append(Finding("MISSING_IN_LOG", "same id as a log fill but different symbol/side, "
                                   "so not the same trade", rows=[r], related=[log.seq]))
        if not same:
            out.append(Finding("MISSING_IN_BROKER", "broker rows with this id have a different symbol/side",
                               [log]))
        elif len({(r.symbol, r.side, r.qty, r.price) for r in same}) > 1:
            out.append(Finding("AMBIGUOUS", "several broker rows share this id and disagree with each other",
                               [log], same))
        else:
            keep = same[0]
            out.append(_compare(log, keep))
            for r in same[1:]:
                out.append(Finding("MISSING_IN_LOG", "duplicate: another copy of the broker fill matched to "
                                   "this log fill (same id, symbol, side, qty, price)", rows=[r], related=[log.seq]))
    return out, used_logs, used_rows


def _components(logs, rows, tol_ns):
    """Connected components of the candidate graph, for logs/rows that share (symbol, side, qty, price)."""
    parent: dict = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for n in [("L", e.seq) for e in logs] + [("R", r.row) for r in rows]:
        parent[n] = n
    bucket = defaultdict(lambda: ([], []))
    for e in logs:
        bucket[(e.symbol, e.side, e.qty, e.price)][0].append(e)
    for r in rows:
        bucket[(r.symbol, r.side, r.qty, r.price)][1].append(r)
    for ls, rs in bucket.values():
        for e in ls:
            for r in rs:
                if abs(r.ts - e.ts) <= tol_ns:
                    parent[find(("L", e.seq))] = find(("R", r.row))
    comps = defaultdict(lambda: ([], []))
    for e in logs:
        comps[find(("L", e.seq))][0].append(e)
    for r in rows:
        comps[find(("R", r.row))][1].append(r)
    return comps.values()


def reconcile(log_fills, rows, tolerance_s: int = DEFAULT_TOLERANCE_S, rejected=None) -> dict:
    rejected = rejected or {}
    findings, used_logs, used_rows = _by_id(log_fills, rows, rejected)
    rest_logs = [e for e in log_fills if e.seq not in used_logs]
    rest_rows = [r for r in rows if r.client_order_id is None]
    for ls, rs in _components(rest_logs, rest_rows, tolerance_s * 10**9):
        if len(ls) == 1 and len(rs) == 1:
            findings.append(Finding("MATCHED", f"no id; same symbol, side, qty {ls[0].qty}, price {ls[0].price}, "
                                    f"within {tolerance_s}s", ls, rs))
        elif ls and rs:
            findings.append(Finding(
                "AMBIGUOUS", f"{len(ls)} log fill(s) and {len(rs)} id-less broker row(s) are candidates for each "
                f"other (same symbol, side, qty, price, within {tolerance_s}s); not paired", ls, rs))
        else:
            for e in ls:
                findings.append(Finding("MISSING_IN_BROKER", "ACCEPTED in the log; no broker row matches by id "
                                        "or by symbol/side/qty/price within tolerance", [e]))
            for r in rs:
                findings.append(Finding("MISSING_IN_LOG", "id-less broker row; no log fill matches by "
                                        "symbol/side/qty/price within tolerance", rows=[r]))
    findings.sort(key=Finding.sort_key)
    counts = {c: 0 for c in CATEGORIES}
    for f in findings:
        counts[f.category] += 1
    return {
        "status": "ok",
        "policy": {"match": "client_order_id when the broker row has one, else (symbol, side, qty, price) "
                            "within tolerance; candidate sets larger than one-to-one are AMBIGUOUS, never paired",
                   "tolerance_seconds": tolerance_s, "tolerance_inclusive": True,
                   "order": "findings sorted by (first log seq, first row ts, row content, category)"},
        "log_fills": len(log_fills),
        "csv_rows": len(rows),
        "counts": counts,
        "findings": [f.as_dict() for f in findings],
        "implied_difference": _implied(findings),
    }
