"""Tamper demo on a COPY of the log. The real log is only read."""

import tempfile
from pathlib import Path

import orjson

from core.logio import ChainError, scan

MAX_BYTES = 64 << 20


class NothingToTamper(Exception):
    pass


def tamper_copy(log: Path) -> dict:
    """Copy `log` to a temp file, change qty by +1 on one server-chosen line (the middle one), verify the copy with the
    same verifier the auditor uses, and report the first bad seq. The path is never taken from a client."""
    if not log.exists() or log.stat().st_size == 0:
        raise NothingToTamper("the log is empty; run the demo first")
    if log.stat().st_size > MAX_BYTES:
        raise NothingToTamper("the log is too large to copy for a tamper demo")
    lines = log.read_bytes().splitlines(keepends=True)
    seq = max(1, len(lines) // 2)
    ev = orjson.loads(lines[seq - 1])
    before, ev["qty"] = ev["qty"], ev["qty"] + 1
    lines[seq - 1] = orjson.dumps(ev, option=orjson.OPT_SORT_KEYS) + b"\n"
    with tempfile.TemporaryDirectory(prefix="ts-tamper-") as d:
        copy = Path(d) / "tampered.log"
        copy.write_bytes(b"".join(lines))
        verified, err = 0, None
        try:
            for _ in scan(copy):
                verified += 1
        except ChainError as e:
            err = e
    return {
        "tampered_seq": seq, "field": "qty", "before": before, "after": ev["qty"], "tampered_event": ev,
        "events_total": len(lines), "events_verified_before_break": verified,
        "chain_ok": err is None, "first_bad_seq": err.seq if err else None,
        "reason": err.reason if err else None, "message": str(err) if err else "chain verifies",
        "note": "Edited a temporary copy; the real log was not modified.",
    }
