"""Verify the chain, rebuild state, and re-check every logged decision with the same check()."""

from collections import Counter
from dataclasses import dataclass, field

from core.book import Book
from core.logio import ChainError, scan


@dataclass
class Audit:
    book: Book
    events: int = 0
    accepted: int = 0
    rejects: Counter = field(default_factory=Counter)
    findings: list = field(default_factory=list)
    chain_error: ChainError | None = None


def audit(log_path, rules) -> Audit:
    a = Audit(Book(rules))
    book, prev_ts = a.book, 0
    try:
        for ev in scan(log_path):
            v = book.verdict(ev, ev.ts)
            if (v.accept, v.code) != (ev.decision == "ACCEPT", ev.reason_code):
                a.findings.append({"seq": ev.seq, "kind": "DECISION_MISMATCH",
                                   "detail": f"logged {ev.decision}/{ev.reason_code}, rules say "
                                             f"{'ACCEPT' if v.accept else 'REJECT'}/{v.code}"})
            if ev.client_order_id in book.seen:
                a.findings.append({"seq": ev.seq, "kind": "DUPLICATE_ID",
                                   "detail": f"{ev.client_order_id} already logged at seq "
                                             f"{book.seen[ev.client_order_id].seq}"})
            if ev.ts < prev_ts:
                a.findings.append({"seq": ev.seq, "kind": "TIME_REGRESSION",
                                   "detail": f"ts {ev.ts} is before {prev_ts}"})
            book.apply(ev)
            prev_ts, a.events = ev.ts, a.events + 1
            if ev.decision == "ACCEPT":
                a.accepted += 1
            else:
                a.rejects[ev.reason_code] += 1
    except ChainError as e:
        a.chain_error = e
    return a
