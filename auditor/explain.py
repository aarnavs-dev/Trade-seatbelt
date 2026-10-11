"""Deterministic explanations of every REJECT in a gate log, and of every skip in a copier.log. No AI, no network.

Inputs are the log and the run's rules.yaml only: the state at each REJECT is rebuilt by replaying the verified events
through the same Book (and the same check() via Book.verdict) the gate used, so the explanation never depends on where
an order came from. check() returns only a code and a sentence, so the numbers it does not expose (the rate window, the
band, the losing streak behind a cooldown) are computed here from the replayed Book.

  python -m auditor.explain <run id or run dir> [--json] [--max N]
"""

import argparse
import json
import sys
from collections import deque
from pathlib import Path

from core.book import WINDOW_NS, Book
from core.logio import scan
from core.money import fmt
from core.rules import Rules, load_rules

PROTECTS = {
    "KILL_SWITCH": ("kill switch", "stops all trading at once when you have decided to halt"),
    "RATE_LIMIT": ("max orders per minute", "runaway loops and panic clicking that send many orders in a short time"),
    "PRICE_BAND": ("price band", "fat-finger prices far from the reference price"),
    "MAX_POSITION": ("max position per symbol", "a position larger than you are willing to hold"),
    "MAX_DAILY_LOSS": ("max daily loss", "adding new risk after you have already lost your daily limit"),
    "LOSS_STREAK_COOLDOWN": ("loss-streak cooldown", "revenge trading right after several losing trades in a row"),
    "BAD_ORDER": ("order sanity", "orders that cannot be valid: unknown symbol, zero or negative size or price"),
}
COPIER_PROTECTS = {
    "INVALID_SIGNAL": "acting on a malformed or tampered signal",
    "UNTRUSTED_LEADER": "copying someone you did not choose to follow",
    "DUPLICATE_SIGNAL": "executing the same signal twice",
    "UNKNOWN_SYMBOL": "trading something this follower has no limits or price anchor for",
    "MARKET_CLOSED": "orders when the market is closed",
    "STALE_SIGNAL": "acting on old information",
    "FEED_GAP": "copying a signal that may depend on signals that never arrived",
    "LEADER_DRAWDOWN_STOP": "following a leader who is deep in a drawdown",
    "SIZE_ROUNDS_TO_ZERO": "a copied order that is too small to exist after scaling",
}
MAX_LISTED = 5


def _ns_s(ns):
    return f"{ns / 10**9:.3f}s"


def _list(items):
    """['seq 3 (-$1.20)', ...] -> deterministic short text with an '... and N earlier' tail."""
    extra = len(items) - MAX_LISTED
    shown = items[-MAX_LISTED:]
    return (f"{extra} earlier, " if extra > 0 else "") + ", ".join(shown)


class _Tracker:
    """Replays events through a Book and remembers what the Book itself forgets: per-fill results and who caused what."""

    def __init__(self, rules: Rules):
        self.book = Book(rules)
        self.window: deque = deque()         # (ts, seq) of events counted in the rate window (as Book.order_times)
        self.streak: list = []               # losing closes since the last win / trigger: (seq, net)
        self.losses: list = []               # every losing close: (seq, net)
        self.trigger = None                  # (seq of the closing loss that set cooldown_until, [(seq, net)...])

    def apply(self, ev):
        b = self.book
        pos0, gross0 = b.ledger.position(ev.symbol), b.ledger.realized_gross
        cd0 = b.cooldown_until
        if ev.reason_code != "RATE_LIMIT":
            self.window.append((ev.ts, ev.seq))
        while self.window and self.window[0][0] <= ev.ts - WINDOW_NS:
            self.window.popleft()
        b.apply(ev)
        if ev.decision != "ACCEPT":
            return
        signed = ev.qty if ev.side == "BUY" else -ev.qty
        if pos0 != 0 and (pos0 > 0) != (signed > 0):                       # the fill closed something
            net = b.ledger.realized_gross - gross0 - b.rules.fees.of(ev.qty)
            if net < 0:
                self.streak.append((ev.seq, net))
                self.losses.append((ev.seq, net))
                if b.cooldown_until != cd0:
                    self.trigger = (ev.seq, list(self.streak))
                    self.streak = []
            else:
                self.streak = []


def _explain_one(t: _Tracker, ev) -> dict:
    b, r = t.book, t.book.rules
    code = ev.reason_code
    rule, protects = PROTECTS.get(code, (code, "an order the rules do not allow"))
    pos = b.ledger.position(ev.symbol)
    new_pos = pos + (ev.qty if ev.side == "BUY" else -ev.qty)
    verdict = b.verdict(ev, ev.ts)
    d = {"seq": ev.seq, "client_order_id": ev.client_order_id, "reason_code": code, "rule": rule, "protects_against": protects,
         "order": f"{ev.side} {ev.qty} {ev.symbol} @ {fmt(ev.price)}", "limit": None, "value_before": None,
         "value_if_accepted": None, "causes": [], "replay_agrees": (not verdict.accept) and verdict.code == code}
    if code == "MAX_POSITION":
        d.update(limit=f"{r.max_position_per_symbol} shares", value_before=f"{pos} shares",
                 value_if_accepted=f"{new_pos} shares", limit_n=r.max_position_per_symbol, before_n=pos, after_n=new_pos)
        d["detail"] = f"{ev.symbol} would go from {pos} to {new_pos} shares; the limit is {r.max_position_per_symbol}."
    elif code == "PRICE_BAND":
        ref = r.symbols[ev.symbol].reference_price
        dev_bp = abs(ev.price - ref) * 10_000 // ref
        lo, hi = ref - ref * r.price_band_pct // 100, ref + ref * r.price_band_pct // 100
        d.update(limit=f"{r.price_band_pct}% from the reference {fmt(ref)} (allowed {fmt(lo)} to {fmt(hi)})",
                 value_before=f"reference {fmt(ref)}", value_if_accepted=f"{dev_bp / 100:.2f}% away ({dev_bp} bp) at {fmt(ev.price)}",
                 limit_n=r.price_band_pct, after_bp=dev_bp, ref_n=ref)
        d["detail"] = f"{fmt(ev.price)} is {dev_bp / 100:.2f}% from the reference {fmt(ref)}; the band is {r.price_band_pct}%."
    elif code == "RATE_LIMIT":
        n = len(t.window)
        seqs = [s for _, s in t.window]
        d.update(limit=f"{r.max_orders_per_minute} orders per 60 s", value_before=f"{n} orders in the last 60 s",
                 value_if_accepted=f"{n + 1} orders in 60 s", limit_n=r.max_orders_per_minute, before_n=n, after_n=n + 1)
        d["detail"] = f"{n} orders were logged in the 60 s before this one (seq {seqs[0]} to {seqs[-1]}); the limit is {r.max_orders_per_minute}."
        d["causes"] = [seqs[0], seqs[-1]]
    elif code == "MAX_DAILY_LOSS":
        loss = -b.realized_net
        d.update(limit=f"{fmt(r.max_daily_loss)} realized loss", value_before=f"{fmt(loss)} realized loss (after fees)",
                 value_if_accepted=f"{fmt(loss)} realized loss and |position| growing {pos} -> {new_pos}",
                 limit_n=r.max_daily_loss, before_n=loss)
        d["detail"] = (f"Realized loss is {fmt(loss)}, at or past the {fmt(r.max_daily_loss)} daily limit, and this order would grow "
                       f"the {ev.symbol} position from {pos} to {new_pos}. Orders that reduce a position are still allowed.")
        d["causes"] = [s for s, _ in t.losses[-MAX_LISTED:]]
        d["cause_text"] = f"{len(t.losses)} losing close(s): " + _list([f"seq {s} ({fmt(n)})" for s, n in t.losses])
    elif code == "LOSS_STREAK_COOLDOWN":
        cd = r.loss_streak_cooldown
        left = b.cooldown_until - ev.ts
        d.update(limit=f"{cd.losses} losing trades in a row -> {cd.cooldown_seconds}s cooldown",
                 value_before=f"cooldown ends in {_ns_s(left)}", value_if_accepted=f"|position| growing {pos} -> {new_pos} during the cooldown",
                 limit_n=cd.losses, before_n=left)
        d["detail"] = f"A cooldown is active for another {_ns_s(left)}; this order would grow the {ev.symbol} position from {pos} to {new_pos}."
        if t.trigger and t.trigger[0] is not None:
            tseq, streak = t.trigger
            d["causes"] = [s for s, _ in streak]
            d["cause_text"] = (f"{len(streak)} losing trades in a row triggered the cooldown at seq {tseq}: "
                               + ", ".join(f"seq {s} ({fmt(n)})" for s, n in streak))
    elif code == "KILL_SWITCH":
        d.update(limit="kill_switch: true", value_before="kill switch on", value_if_accepted="every order is refused while it is on")
        d["detail"] = "The kill switch in rules.yaml is on."
    else:  # BAD_ORDER
        d.update(limit="known symbol, qty > 0, price > 0", value_before=ev.reason_text, value_if_accepted="not a valid order")
        d["detail"] = f"{ev.reason_text}."
    if not d["replay_agrees"]:
        d["detail"] += f" WARNING: replaying the rules gives {'ACCEPT' if verdict.accept else 'REJECT'}/{verdict.code}, not the logged {code}."
    d["text"] = _text(d)
    return d


def _text(d) -> str:
    lines = [f"REJECT seq {d['seq']}  {d['order']}  [{d['client_order_id']}]",
             f"  rule        {d['rule']} ({d['reason_code']}): protects against {d['protects_against']}.",
             f"  limit       {d['limit']}",
             f"  at that moment   {d['value_before']}",
             f"  if accepted      {d['value_if_accepted']}",
             f"  why         {d['detail']}"]
    if d.get("cause_text"):
        lines.append(f"  caused by   {d['cause_text']}")
    return "\n".join(lines)


def explain_events(events, rules: Rules) -> list[dict]:
    t, out = _Tracker(rules), []
    for ev in events:
        if ev.decision == "REJECT":
            out.append(_explain_one(t, ev))
        t.apply(ev)
    return out


def explain_log(log_path, rules: Rules) -> list[dict]:
    """Raises core.logio.ChainError if the log does not verify (an explanation of a tampered log would be worthless)."""
    return explain_events(scan(log_path), rules)


def explain_copier_log(path) -> list[dict]:
    from copier import ledger
    out = []
    for ln in ledger.read(path):
        code = ln["reason_code"]
        d = {"kind": "copier_skip", "seq": ln["seq"], "signal_id": ln["signal_id"], "reason_code": code,
             "protects_against": COPIER_PROTECTS.get(code, "an unsafe copy"), "detail": ln["reason_text"]}
        d["text"] = (f"SKIPPED copier.log line {d['seq']}  signal {d['signal_id']}  [{code}]\n"
                     f"  protects against {d['protects_against']}.\n  why         {d['detail']}\n"
                     f"  This signal never reached the gate.")
        out.append(d)
    return out


def render(e: dict) -> str:
    return "\n".join("  " + ln if not ln.startswith("  ") else ln for ln in e["text"].split("\n"))


def resolve_run(arg) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p
    root = Path(__file__).resolve().parent.parent / "runs" / arg
    if root.is_dir():
        return root
    raise SystemExit(f"no such run: {arg}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="auditor.explain", description=__doc__.split("\n\n")[0])
    ap.add_argument("run", help="run id (under runs/) or a run directory containing gate.log and rules.yaml")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max", type=int, help="print at most this many explanations of each kind")
    a = ap.parse_args(argv)
    d = resolve_run(a.run)
    try:
        gate = explain_log(d / "gate.log", load_rules(d / "rules.yaml")) if (d / "gate.log").exists() else []
        skips = explain_copier_log(d / "copier.log")
    except Exception as e:  # ChainError / LedgerError: say so instead of explaining a damaged log
        print(f"cannot explain {d.name}: {e}", file=sys.stderr)
        return 1
    if a.max is not None:
        gate, skips = gate[:a.max], skips[:a.max]
    if a.json:
        print(json.dumps({"rejects": gate, "copier_skips": skips}, indent=2, sort_keys=True))
        return 0
    print(f"{d.name}: {len(gate)} gate REJECT(s), {len(skips)} copier skip(s). Deterministic; computed from the logs and rules.yaml only.")
    for e in gate + skips:
        print("\n" + render(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
