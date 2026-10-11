import json

import pytest

from auditor.replay import audit
from core.logio import ChainError, scan
from tests.helpers import RULES, line, run, run_gate

LINES = [line(f"O{i}", "BUY" if i % 2 else "SELL", "AAPL", 5, 19000) for i in range(1, 11)]


@pytest.fixture
def log(tmp_path):
    p = tmp_path / "g.log"
    run(run_gate(p, LINES))
    return p


def first_bad(path):
    try:
        list(scan(path))
    except ChainError as e:
        return e.seq
    return None


def test_clean_log_verifies(log):
    assert first_bad(log) is None and audit(log, RULES).chain_error is None


@pytest.mark.parametrize("k", [1, 4, 10])
def test_edit_detected_at_that_seq(log, k):
    rows = log.read_bytes().splitlines(keepends=True)
    d = json.loads(rows[k - 1]); d["price"] += 1
    rows[k - 1] = (json.dumps(d, sort_keys=True, separators=(",", ":")) + "\n").encode()
    log.write_bytes(b"".join(rows))
    assert first_bad(log) == k


def test_edit_with_recomputed_hash_caught_at_next_line(log):
    from core.canon import event_hash
    rows = log.read_bytes().splitlines(keepends=True)
    d = json.loads(rows[2]); d["qty"] += 1
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
    log.write_bytes(data[:-10])
    assert first_bad(log) == 10
    log.write_bytes(data + b"garbage\n")
    assert first_bad(log) == 11


def test_known_gap_tail_truncation_is_not_detected(log):
    rows = log.read_bytes().splitlines(keepends=True)
    log.write_bytes(b"".join(rows[:-3]))
    assert first_bad(log) is None  # documented limitation: no external anchor for the head hash


def test_auditor_reports_first_bad_seq_and_partial_figures(log):
    rows = log.read_bytes().splitlines(keepends=True)
    rows[6] = rows[6].replace(b'"qty":5', b'"qty":6')
    log.write_bytes(b"".join(rows))
    a = audit(log, RULES)
    assert a.chain_error.seq == 7 and a.events == 6
