import json

import pytest

from auditor.replay import audit
from core.logio import ChainError, scan
from tests.helpers import RULES, line, run, run_gate

LINES = [line(f"O{i}", "BUY" if i % 2 else "SELL", "AAPL", 5, 19000) for i in range(1, 11)]


@pytest.fixture(params=["v1", "v2"])
def log(tmp_path, request):
    """The same 10 orders as a v1 log (10 lines) and as a v2 log (a rules record, then the 10 orders: 11 lines). Every
    tamper test below runs on both; line k of the file is seq k in both."""
    p = tmp_path / "g.log"
    run(run_gate(p, LINES, record_rules=request.param == "v2"))
    return p


def n_lines(path):
    return len(path.read_bytes().splitlines())


def rewrite(row, **changes):
    d = json.loads(row)
    d.update(changes)
    return (json.dumps(d, sort_keys=True, separators=(",", ":")) + "\n").encode()


def first_bad(path):
    try:
        list(scan(path))
    except ChainError as e:
        return e.seq
    return None


def test_clean_log_verifies(log):
    assert first_bad(log) is None and audit(log, RULES).chain_error is None


@pytest.mark.parametrize("k", [1, 4, "last"])
def test_edit_detected_at_that_seq(log, k):
    k = n_lines(log) if k == "last" else k  # line 1 is the rules record in a v2 log: editing it is caught like any edit
    rows = log.read_bytes().splitlines(keepends=True)
    rows[k - 1] = rewrite(rows[k - 1], ts=json.loads(rows[k - 1])["ts"] + 1)  # ts exists on orders and on the rules record
    log.write_bytes(b"".join(rows))
    assert first_bad(log) == k


def test_edit_with_recomputed_hash_caught_at_next_line(log):
    from core.canon import event_hash
    rows = log.read_bytes().splitlines(keepends=True)
    d = json.loads(rows[2]); d["ts"] += 1
    d["hash"] = event_hash(d["prev_hash"], {k: v for k, v in d.items() if k not in ("hash", "prev_hash")})
    rows[2] = (json.dumps(d, sort_keys=True, separators=(",", ":")) + "\n").encode()
    log.write_bytes(b"".join(rows))
    assert first_bad(log) == 4


@pytest.mark.parametrize("k", [1, 5, 9])
def test_delete_detected(log, k):
    rows = log.read_bytes().splitlines(keepends=True)
    del rows[k - 1]
    log.write_bytes(b"".join(rows))
    assert first_bad(log) == k


@pytest.mark.parametrize("k", [1, 6])
def test_reorder_detected(log, k):
    rows = log.read_bytes().splitlines(keepends=True)
    rows[k - 1], rows[k] = rows[k], rows[k - 1]
    log.write_bytes(b"".join(rows))
    assert first_bad(log) == k


def test_whitespace_change_detected(log):
    rows = log.read_bytes().splitlines(keepends=True)
    rows[3] = rows[3].replace(b'"seq":4,', b'"seq": 4,')
    log.write_bytes(b"".join(rows))
    assert first_bad(log) == 4


def test_partial_last_line_and_garbage(log):
    data = log.read_bytes()
    n = n_lines(log)
    log.write_bytes(data[:-10])
    assert first_bad(log) == n
    log.write_bytes(data + b"garbage\n")
    assert first_bad(log) == n + 1


def test_known_gap_tail_truncation_is_not_detected(log):
    rows = log.read_bytes().splitlines(keepends=True)
    log.write_bytes(b"".join(rows[:-3]))
    assert first_bad(log) is None  # documented limitation: no external anchor for the head hash


def test_auditor_reports_first_bad_seq_and_partial_figures(log):
    rows = log.read_bytes().splitlines(keepends=True)
    rows[6] = rows[6].replace(b'"qty":5', b'"qty":6')
    log.write_bytes(b"".join(rows))
    a = audit(log, RULES)
    orders_before_line_7 = 6 - len(a.rules_records)  # a v2 log spends line 1 on the rules record
    assert a.chain_error.seq == 7 and a.events == orders_before_line_7


# ---- the rules record (v2) ------------------------------------------------------------------------------------------

def test_a_v1_log_verifies_unchanged_and_says_its_rules_are_not_recorded(tmp_path):
    p = tmp_path / "v1.log"
    run(run_gate(p, LINES, record_rules=False))
    first = json.loads(p.read_bytes().splitlines()[0])
    assert first["seq"] == 1 and "type" not in first  # no control record anywhere in a v1 log
    a = audit(p, RULES)
    assert a.chain_error is None and a.findings == [] and a.events == 10 and a.rules_records == []
    from auditor.report import build_report
    rr = build_report(a, p)["rules_record"]
    assert rr["status"] == "NOT_RECORDED" and rr["log_version"] == 1 and rr["unrecorded_events"] == 10


def test_editing_the_rules_record_is_caught_even_if_its_hash_is_recomputed(tmp_path):
    p = tmp_path / "g.log"
    run(run_gate(p, LINES))
    rows = p.read_bytes().splitlines(keepends=True)
    forged = json.loads(rows[0])
    assert forged["type"] == "RULES"
    forged["rules_hash"] = "0" * 64
    assert first_bad_after(p, rows, forged, recompute=False) == 1       # plain edit: its own hash no longer matches
    assert first_bad_after(p, rows, forged, recompute=True) == 2        # re-hashed: the NEXT line's prev_hash gives it away


def first_bad_after(p, rows, rec, recompute):
    from core.canon import event_hash
    rec = dict(rec)
    if recompute:
        rec["hash"] = event_hash(rec["prev_hash"], {k: v for k, v in rec.items() if k not in ("hash", "prev_hash")})
    out = list(rows)
    out[0] = (json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n").encode()
    p.write_bytes(b"".join(out))
    return first_bad(p)


def test_the_rules_record_is_a_chained_line_not_an_order(log):
    rows = [json.loads(r) for r in log.read_bytes().splitlines()]
    recs = [r for r in rows if "type" in r]
    assert len(recs) == (1 if len(rows) == 11 else 0)
    assert [e.seq for e in scan(log)] == [r["seq"] for r in rows if "type" not in r]  # scan() yields orders only
    a = audit(log, RULES)
    assert a.events == 10 and a.accepted + sum(a.rejects.values()) == 10  # a record is never counted as an order
