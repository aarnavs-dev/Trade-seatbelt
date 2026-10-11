"""copier.log: append-only, hash-chained, one line per signal the copier did NOT send to the gate.
Line = {seq, signal_id, reason_code, reason_text, prev_hash, hash}; hash = sha256(prev_hash || canonical JSON of the rest),
the same construction as the gate log. It never writes the gate log.

Restart: the copier recomputes every skip from the feed. `append` checks the recomputed skip against the line already on
disk at that position (it must be identical) and writes only the lines that are missing."""

import os
from pathlib import Path

import orjson

from core.canon import GENESIS, canon, event_hash

FIELDS = ("seq", "signal_id", "reason_code", "reason_text")


class LedgerError(Exception):
    pass


def _line(body, prev):
    h = event_hash(prev, body)
    return canon({**body, "prev_hash": prev, "hash": h}) + b"\n", h


def read(path) -> list[dict]:
    """Verified lines. A partial last line (killed mid-write) is ignored; any other damage raises LedgerError."""
    p = Path(path)
    if not p.exists():
        return []
    out, prev = [], GENESIS
    data = p.read_bytes()
    lines = data.split(b"\n")
    lines.pop()   # whatever follows the last newline is a torn tail (or empty)
    for n, raw in enumerate(lines, 1):
        try:
            d = orjson.loads(raw)
            body = {k: d[k] for k in FIELDS}
            ok = (set(d) == set(FIELDS) | {"prev_hash", "hash"} and d["seq"] == n and d["prev_hash"] == prev
                  and event_hash(prev, body) == d["hash"] and raw + b"\n" == _line(body, prev)[0])
        except (orjson.JSONDecodeError, KeyError, TypeError):
            ok = False
        if not ok:
            raise LedgerError(f"copier.log is damaged at line {n}")
        out.append(d)
        prev = d["hash"]
    return out


class CopierLog:
    def __init__(self, path):
        self.path = Path(path)
        self.lines = read(self.path)
        self._pos = 0                       # how many skips this run has produced so far
        self.prev = self.lines[-1]["hash"] if self.lines else GENESIS
        if self.path.exists():              # drop a torn tail so the next append starts on a clean line
            with open(self.path, "rb+") as f:
                data = f.read()
                keep = data.rfind(b"\n") + 1
                if keep != len(data):
                    f.truncate(keep)
        self.f = open(self.path, "ab")

    def append(self, signal_id, reason_code, reason_text) -> bool:
        """Record one skip. Returns True if a line was written, False if the identical line was already there."""
        self._pos += 1
        body = {"seq": self._pos, "signal_id": signal_id, "reason_code": reason_code, "reason_text": reason_text}
        if self._pos <= len(self.lines):
            old = self.lines[self._pos - 1]
            if {k: old[k] for k in FIELDS} != body:
                raise LedgerError(f"copier.log line {self._pos} differs from what this feed produces now; "
                                  "the feed, config or rules changed since the log was started")
            return False
        raw, h = _line(body, self.prev)
        self.f.write(raw)
        self.f.flush()
        os.fsync(self.f.fileno())
        self.lines.append({**body, "prev_hash": self.prev, "hash": h})
        self.prev = h
        return True

    def close(self):
        self.f.close()
