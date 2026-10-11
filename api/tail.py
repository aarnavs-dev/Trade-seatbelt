"""Tail a hash-chained log as server-sent events. Read-only: the file is only ever opened with 'rb'."""

import asyncio
import os
from pathlib import Path
from typing import AsyncIterator, Callable

import orjson

CHUNK = 1 << 20


def sse(event: str, data, id_: str | int | None = None) -> bytes:
    out = b""
    if id_ is not None:
        out += b"id: " + str(id_).encode() + b"\n"
    return out + b"event: " + event.encode() + b"\ndata: " + orjson.dumps(data) + b"\n\n"


def window_start(path: Path, tail: int) -> tuple[int, int]:
    """(byte offset where the last `tail` complete lines begin, total complete lines). One pass, no parsing."""
    ends: list[int] = []  # offsets just after each newline, last tail+1 only
    total, pos = 0, 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            i = chunk.find(b"\n")
            while i != -1:
                total += 1
                ends.append(pos + i + 1)
                if len(ends) > tail + 1:
                    del ends[0]
                i = chunk.find(b"\n", i + 1)
            pos += len(chunk)
    if total <= tail:
        return 0, total
    return ends[-(tail + 1)], total


async def events(resolve: Callable[[], Path], *, tail: int = 500, after_seq: int = 0, follow: bool = True,
                 poll: float = 0.1, heartbeat: float = 15.0) -> AsyncIterator[bytes]:
    """Yield SSE frames: `hello` (totals), then one `block` per complete log line, then keep following.
    If the source path changes or the file shrinks, emit `reset` (with an empty id) and stop; the client reopens."""
    path = resolve()
    yield b"retry: 1000\n\n"
    offset, total, line_no = 0, 0, 0
    if path.exists():
        offset, total = window_start(path, tail)
        line_no = max(0, total - tail)  # lines before the window; seq == line number in a valid log
    first_seq = line_no + 1
    yield sse("hello", {"source_file": path.name, "total": total, "first_seq": first_seq if total else None})
    buf, idle, n = b"", 0.0, 0
    while True:
        if resolve() != path:
            yield sse("reset", {"reason": "source changed"}, id_="")
            return
        try:
            size = os.stat(path).st_size
        except FileNotFoundError:
            size = None
        if size is not None and size < offset + len(buf):
            yield sse("reset", {"reason": "log was truncated or replaced"}, id_="")
            return
        got = False
        if size is not None and size > offset + len(buf):
            with open(path, "rb") as f:
                f.seek(offset + len(buf))
                buf += f.read(min(size - offset - len(buf), 4 * CHUNK))
            while (i := buf.find(b"\n")) != -1:  # only complete lines; a torn tail waits for its newline
                raw, buf = buf[:i], buf[i + 1:]
                offset += i + 1
                line_no += 1
                got = True
                if line_no > after_seq:
                    try:
                        yield sse("block", orjson.loads(raw), id_=line_no)
                    except orjson.JSONDecodeError:
                        yield sse("malformed", {"line": line_no}, id_=line_no)
                n += 1
                if n % 500 == 0:
                    await asyncio.sleep(0)
        if got:
            idle = 0.0
            continue
        if not follow:
            return
        await asyncio.sleep(poll)
        idle += poll
        if idle >= heartbeat:
            idle = 0.0
            yield b": ping\n\n"
