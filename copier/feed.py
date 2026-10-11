"""Turns a feed into Incoming records WITHOUT dropping bad ones: the copier must see (and record) what it skips.
A local feed.jsonl line is {"signal": {...}, "via": ..., "tx": ...}; its signer is "local" (not signature-verified)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

LOCAL_SIGNER = "local"


@dataclass(frozen=True)
class Incoming:
    signer: str     # who published it, from the transport (the local feed says "local")
    raw: Any        # the signal object as received, not yet validated
    ref: str        # where it came from, e.g. "line 7"


def read_local(path) -> list[Incoming]:
    out = []
    for n, line in enumerate(Path(path).read_bytes().splitlines(), 1):
        try:
            d = orjson.loads(line)
        except orjson.JSONDecodeError:
            out.append(Incoming(LOCAL_SIGNER, line.decode(errors="replace")[:80], f"line {n}"))
            continue
        if isinstance(d, dict) and "event" in d and "signal" not in d:
            continue          # publisher notes such as chain_disabled
        out.append(Incoming(LOCAL_SIGNER, d.get("signal") if isinstance(d, dict) else d, f"line {n}"))
    return out


def from_feed_result(result) -> list[Incoming]:
    """Adapter for leaders.feed.ChainFeed/LocalFeed results (already validated items)."""
    return [Incoming(i.signer, i.signal.to_dict(), f"{i.slot}/{i.signature[:8]}") for i in result.items]
