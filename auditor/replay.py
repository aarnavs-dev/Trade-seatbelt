"""Verify the chain, rebuild state, and re-check every logged decision with the same check()."""

from collections import Counter
from dataclasses import dataclass, field

from core.book import Book
from core.events import Event
from core.logio import ChainError, scan_records
from core.rules import rules_hash


@dataclass
class Audit:
    book: Book
    events: int = 0
    accepted: int = 0
    rejects: Counter = field(default_factory=Counter)
    findings: list = field(default_factory=list)
    chain_error: ChainError | None = None
    fills: list = field(default_factory=list)      # verified ACCEPT events, in seq order (for reconciliation)
    rejected: dict = field(default_factory=dict)   # client_order_id -> (seq, reason_code) of its first REJECT
    supplied_rules_hash: str = ""                  # hash of the rules the auditor was given
    rules_records: list = field(default_factory=list)  # [(seq, ts, rules_hash)] control records, in order
    unrecorded_events: int = 0                     # order events before the first rules record (all of them in a v1 log)


def audit(log_path, rules) -> Audit:
    a = Audit(Book(rules), supplied_rules_hash=rules_hash(rules))
    book, prev_ts = a.book, 0
    try:
        for ev in scan_records(log_path):
            if not isinstance(ev, Event):  # a control record: check it, apply it, never count it as an order
                if ev.ts < prev_ts:
                    a.findings.append({"seq": ev.seq, "kind": "TIME_REGRESSION", "detail": f"ts {ev.ts} is before {prev_ts}"})
                if ev.rules_hash != a.supplied_rules_hash:
                    a.findings.append({"seq": ev.seq, "kind": "RULES_MISMATCH", "detail":
                                       f"the log records rules {ev.rules_hash[:12]} here but the rules file given to the auditor "
                                       f"hashes to {a.supplied_rules_hash[:12]}; orders after this line were re-checked against "
                                       "the supplied rules, so decisions and P&L below may not reflect what the gate enforced"})
                book.apply_record(ev)
                a.rules_records.append((ev.seq, ev.ts, ev.rules_hash))
                prev_ts = ev.ts
                continue
            if not a.rules_records:
                a.unrecorded_events += 1
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
                a.fills.append(ev)
            else:
                a.rejects[ev.reason_code] += 1
                a.rejected.setdefault(ev.client_order_id, (ev.seq, ev.reason_code))
    except ChainError as e:
        a.chain_error = e
    return a
