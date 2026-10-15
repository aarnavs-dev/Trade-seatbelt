"""One row per feed signal, built from the run's three files and nothing else: feed.jsonl (what arrived, in order),
copier.log (what the copier stopped or clipped) and gate.log (what the gate decided). Read-only and tolerant of files that
are still being written (a partial last line is ignored; a row is emitted only when its outcome is known, in feed order)."""

from pathlib import Path

import orjson

from copier import ledger
from copier.copier import CLIPPED, order_id_for
from copier.feed import read_local
from core.events import Event
from core.logio import parse_line
from leaders.signal import SignalError, validate

COPIED, STOPPED, REJECTED, CLIP = "COPIED", "STOPPED_BY_COPIER", "REJECTED_BY_RULES", "CLIPPED"
EXPLORER = "https://explorer.solana.com/tx/{}?cluster=devnet"


def gate_events(path) -> dict:
    """client_order_id -> Event, from the complete lines of gate.log (not chain-verified: this is a live view)."""
    out = {}
    try:
        data = Path(path).read_bytes()
    except OSError:
        return out
    for raw in data.split(b"\n")[:-1]:
        try:
            ev = parse_line(raw)
        except ValueError:
            break
        if isinstance(ev, Event):  # a control record (rules in force) is not an order
            out[ev.client_order_id] = ev
    return out


def build_rows(run_dir) -> tuple[list[dict], int, int]:
    """-> (resolved rows in feed order, feed length, ledger lines read). Stops at the first signal whose outcome is unknown."""
    d = Path(run_dir)
    feed = read_local(d / "feed.jsonl") if (d / "feed.jsonl").exists() else []
    events = gate_events(d / "gate.log")          # gate first, then the ledger: the ledger snapshot is never older
    try:
        L = ledger.read(d / "copier.log")
    except ledger.LedgerError:
        L = []
    plain = {}
    if any(e.decision == "REJECT" for e in events.values()):     # plain-English reason for rejected rows (same text as the explanation)
        from auditor.explain import explain_events
        from core.rules import load_rules
        try:
            plain = {x["seq"]: x["detail"] for x in explain_events(list(events.values()), load_rules(d / "rules.yaml"))}
        except (OSError, ValueError):
            plain = {}
    p, seen, rows = 0, set(), []
    for n, inc in enumerate(feed, 1):
        raw = inc.raw if isinstance(inc.raw, dict) else {}
        try:
            sig = validate(inc.raw)
            sid, ok = f"{inc.signer}:{sig.leader_seq}", True
        except SignalError:
            sid, ok = f"{inc.signer}:{inc.ref}", False
        first = sid not in seen
        seen.add(sid)

        def cand():
            l = L[p] if p < len(L) else None
            return l if l and l["signal_id"] == sid and not (first and l["reason_code"] == "DUPLICATE_SIGNAL") else None

        clip = None
        l = cand()
        if l and l["reason_code"] == CLIPPED:
            clip, p = l, p + 1
            l = cand()
        row = {"n": n, "signal_id": sid, "leader": inc.signer, "symbol": raw.get("symbol") if ok else None,
               "side": raw.get("side") if ok else None, "leader_qty": raw.get("qty") if ok else None,
               "qty": raw.get("qty") if ok else None, "price": raw.get("ref_price") if ok else None,
               "leader_ts": raw.get("leader_ts") if ok else None, "tx": inc.tx, "slot": inc.slot,
               "explorer_url": EXPLORER.format(inc.tx) if inc.tx else None,
               "gate_seq": None, "ledger_seq": None, "hash": None, "leader_signal": inc.raw if isinstance(inc.raw, dict) else None}
        if l and l["reason_code"] != CLIPPED:
            p += 1
            row.update(status=STOPPED, reason_code=l["reason_code"], reason_text=l["reason_text"], ledger_seq=l["seq"], hash=l["hash"])
        else:
            ev = events.get(order_id_for(sid)) if ok else None
            if ev is None:
                return rows, len(feed), len(L)
            row.update(qty=ev.qty, price=ev.price, gate_seq=ev.seq, hash=ev.hash)
            if ev.decision == "REJECT":
                row.update(status=REJECTED, reason_code=ev.reason_code, reason_text=plain.get(ev.seq, ev.reason_text))
            elif clip:
                row.update(status=CLIP, reason_code=CLIPPED, reason_text=clip["reason_text"], ledger_seq=clip["seq"])
            else:
                row.update(status=COPIED, reason_code="OK", reason_text=f"Within all your limits; paper-filled at the signal price.")
        rows.append(row)
    return rows, len(feed), len(L)
