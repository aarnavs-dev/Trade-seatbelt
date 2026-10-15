"""Append-only log: writer with a fsync policy, and a verifying reader."""

import hashlib
import os
import sys
import time
from typing import Iterator

import orjson
from pydantic import ValidationError

try:
    import fcntl
except ImportError:  # pragma: no cover (Windows)
    fcntl = None

from core.canon import GENESIS, canon, event_hash
from core.events import CONTROL_TYPES, LOG_VERSION, Event, RulesRecord

FSYNC_POLICIES = ("per_order", "batched", "none")
SUPPORTED_LOG_VERSIONS = (LOG_VERSION,)


class ChainError(Exception):
    def __init__(self, seq: int, reason: str):
        super().__init__(f"chain broken at seq {seq}: {reason}")
        self.seq, self.reason = seq, reason


def event_line(ev) -> bytes:
    """The canonical line for an Event or a control record (anything with model_dump())."""
    return canon(ev.model_dump()) + b"\n"


def parse_line(raw: bytes):
    """One line -> Event or a control record (RulesRecord). Raises ValueError if it is neither. No chain checks."""
    try:
        return Event.model_validate_json(raw)  # the hot path: orders are what a log is made of
    except ValidationError:
        pass
    try:
        t = orjson.loads(raw).get("type")
        return CONTROL_TYPES[t].model_validate_json(raw)
    except (orjson.JSONDecodeError, AttributeError, KeyError, ValidationError):
        raise ValueError("line is not a valid event or control record") from None


def scan_records(path) -> Iterator[Event | RulesRecord]:
    """Yield every verified line in order, orders AND control records. Raises ChainError at the first bad line.

    The reported seq is the position where the chain first fails to hold. For an edited line that
    is the line itself; for a deleted or reordered line it is the first position that is wrong.
    """
    if not os.path.exists(path):
        return
    expected, prev = 1, GENESIS
    with open(path, "rb") as f:
        for raw in f:
            if not raw.endswith(b"\n"):
                raise ChainError(expected, "partial last line")
            try:
                ev = parse_line(raw)
            except ValueError:
                raise ChainError(expected, "line is not a valid event") from None
            if ev.seq != expected:
                raise ChainError(expected, f"found seq {ev.seq}, expected {expected} "
                                           "(a line was deleted, inserted or reordered)")
            if ev.prev_hash != prev:
                raise ChainError(expected, "prev_hash does not match the previous line")
            if event_hash(prev, ev.body()) != ev.hash:
                raise ChainError(expected, "hash mismatch, line content was altered")
            if raw != event_line(ev):
                raise ChainError(expected, "line is not in canonical form")
            if not isinstance(ev, Event) and ev.log_version not in SUPPORTED_LOG_VERSIONS:
                raise ChainError(expected, f"unsupported log_version {ev.log_version}")
            prev, expected = ev.hash, expected + 1
            yield ev


def scan(path) -> Iterator[Event]:
    """Yield the verified ORDER events in order. The whole chain is verified, control records included, but they are not
    yielded, so a reader that only cares about orders never sees them (their seq numbers show as gaps)."""
    for rec in scan_records(path):
        if isinstance(rec, Event):
            yield rec


def repair_torn_tail(path) -> dict | None:
    """Remove a partial last line (bytes after the final newline), keeping the bytes.

    Safe for the durability promise: a reply is only sent after the whole batch write and fsync
    returned, so a line that is missing its newline was never acknowledged. Only a missing-newline
    tail is touched; any other corruption is left for scan() to reject. The removed bytes go to
    `<log>.torn.<ns>` and a note to `<log>.recovery.jsonl` (outside the hash chain). The auditor
    never repairs: it reports the same file as `partial last line`.
    """
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, "rb+") as f:
        size = f.seek(0, 2)
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return None
        cut, pos = 0, size
        while pos > 0:  # find the last newline, scanning backwards
            start = max(0, pos - 65536)
            f.seek(start)
            i = f.read(pos - start).rfind(b"\n")
            if i != -1:
                cut = start + i + 1
                break
            pos = start
        f.seek(cut)
        torn = f.read()
        ns = time.time_ns()
        sidecar = f"{path}.torn.{ns}"
        with open(sidecar, "wb") as t:
            t.write(torn)
            t.flush()
            os.fsync(t.fileno())
        note = {"action": "truncated_torn_tail", "wall_time_ns": ns, "log_size_before": size,
                "kept_bytes": cut, "bytes_removed": len(torn), "removed_sha256": hashlib.sha256(torn).hexdigest(),
                "removed_saved_as": os.path.basename(sidecar)}
        with open(f"{path}.recovery.jsonl", "ab") as r:
            r.write(canon(note) + b"\n")
            r.flush()
            os.fsync(r.fileno())
        f.truncate(cut)
        f.flush()
        os.fsync(f.fileno())
    return note


class LogWriter:
    """policy: per_order and batched both fsync before the caller is answered (the gate decides
    how many events share one fsync); none never fsyncs and is for benchmarks only.
    full_fsync (macOS only): use F_FULLFSYNC instead of os.fsync."""

    def __init__(self, path, policy: str, full_fsync: bool = False):
        if policy not in FSYNC_POLICIES:
            raise ValueError(f"fsync policy must be one of {FSYNC_POLICIES}")
        if full_fsync and not (sys.platform == "darwin" and hasattr(fcntl, "F_FULLFSYNC")):
            raise ValueError("full_fsync (F_FULLFSYNC) is only available on macOS")
        self.policy, self.full_fsync = policy, full_fsync
        self.fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            view = view[os.write(self.fd, view):]
        if self.policy != "none":
            if self.full_fsync:  # macOS: also flush the drive's own cache
                fcntl.fcntl(self.fd, fcntl.F_FULLFSYNC)
            else:  # on macOS this hands data to the drive but does not force its cache
                os.fsync(self.fd)

    def close(self) -> None:
        os.close(self.fd)
