"""Turn a cProfile dump of the gate (taken only during the measured window) into time shares."""

import pstats

CATS = ["fsync", "log write syscall", "sha256 hashing", "orjson + pydantic (parse, validate, dump)",
        "socket send (StreamWriter.write)", "check / book / ledger code", "gate engine + server code",
        "asyncio queues, streams, tasks (Python)", "event loop core, socket reads, idle"]


def category(key) -> str:
    fn, _, name = key
    if "posix.fsync" in name:
        return CATS[0]
    if "posix.write" in name:
        return CATS[1]
    if "sha256" in name or "hexdigest" in name or "_hashlib" in name:
        return CATS[2]
    if "orjson" in name or "pydantic" in fn or "pydantic_core" in name:
        return CATS[3]
    if fn.endswith("asyncio/streams.py") and name == "write":
        return CATS[4]    # StreamWriter.write: its self-time is the transport's send() call
    if "/core/" in fn:
        return CATS[5]
    if "/gate/" in fn:
        return CATS[6]
    if "/asyncio/queues.py" in fn or "/asyncio/streams.py" in fn or "/asyncio/tasks.py" in fn:
        return CATS[7]
    return CATS[8]


def analyze(path: str) -> dict:
    st = pstats.Stats(path)
    total, shares, calls = st.total_tt, dict.fromkeys(CATS, 0.0), {}
    for key, (cc, nc, tt, ct, _) in st.stats.items():
        c = category(key)
        shares[c] += tt
        if "posix.fsync" in key[2]:
            calls["fsync_calls"] = nc
    return {"profiled_seconds": total, "share_pct": {c: 100 * v / total for c, v in shares.items()},
            "seconds": shares, **calls}
