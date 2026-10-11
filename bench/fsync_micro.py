"""Cost of one append + sync on this machine's disk: os.fsync vs fcntl F_FULLFSYNC (macOS)."""

import fcntl
import json
import os
import statistics
import sys
import tempfile
import time

N = 300


def main() -> None:
    d = tempfile.mkdtemp(prefix="fsync_micro_")
    fd = os.open(os.path.join(d, "t"), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    out = {"calls": N, "bytes_per_write": 330, "dir": "temp dir on the same volume as the benchmark logs"}
    for name, fn in (("os.fsync", lambda: os.fsync(fd)),
                     ("F_FULLFSYNC", lambda: fcntl.fcntl(fd, fcntl.F_FULLFSYNC))):
        ts = []
        for _ in range(N):
            os.write(fd, b"x" * 330)
            t = time.perf_counter()
            fn()
            ts.append((time.perf_counter() - t) * 1e6)
        ts.sort()
        out[name] = {"median_us": statistics.median(ts), "p99_us": ts[int(N * 0.99) - 1], "max_us": ts[-1]}
    path = os.path.join(os.path.dirname(__file__), "results", time.strftime("fsync_micro_%Y%m%d_%H%M%S.json"))
    json.dump(out, open(path, "w"), indent=1)
    print(json.dumps(out, indent=1), file=sys.stderr)
    print(path)


if __name__ == "__main__":
    main()
