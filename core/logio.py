"""Append-only log: writer with a fsync policy, and a verifying reader."""

import os
import sys
from typing import Iterator

from pydantic import ValidationError

try:
    import fcntl
except ImportError:  # pragma: no cover (Windows)
    fcntl = None

from core.canon import GENESIS, canon, event_hash
from core.events import Event

FSYNC_POLICIES = ("per_order", "batched", "none")


class ChainError(Exception):
    def __init__(self, seq: int, reason: str):
        super().__init__(f"chain broken at seq {seq}: {reason}")
        self.seq, self.reason = seq, reason


def event_line(ev: Event) -> bytes:
    return canon(ev.model_dump()) + b"\n"


def scan(path) -> Iterator[Event]:
    """Yield verified events in order. Raises ChainError at the first bad line.

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
                ev = Event.model_validate_json(raw)
            except ValidationError:
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
            prev, expected = ev.hash, expected + 1
            yield ev


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
