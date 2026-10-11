"""Rate limiting reads only logged timestamps, so the auditor reproduces the gate's decisions."""

from auditor.replay import audit
from core.logio import scan
from tests.helpers import RULES, counter_clock, line, run, run_gate

SEC = 10**9


def test_rate_limit_replays_identically_from_logged_ts(tmp_path):
    rules = RULES.model_copy(update={"max_orders_per_minute": 3})
    p = tmp_path / "g.log"
    lines = [line(f"R{i}", "BUY", "AAPL", 1, 19000) for i in range(7)]
    out, gate = run(run_gate(p, lines, rules=rules, clock=counter_clock(15 * SEC)))
    # orders at t = 0,15,30,45,60,75,90 s. Window is (ts-60s, ts]; rate-limited orders are not
    # counted. t=45 sees 3 -> RATE_LIMIT; t=60 sees {15,30} -> ok; and so on (worked by hand).
    assert [o["reason_code"] for o in out] == ["OK", "OK", "OK", "RATE_LIMIT", "OK", "OK", "OK"]

    a = audit(p, rules)  # re-decides every event from its logged ts, with no wall clock
    assert a.chain_error is None and a.findings == []
    assert [e.reason_code for e in scan(p)] == [o["reason_code"] for o in out]
    assert a.book.state_hash() == gate.book.state_hash()
