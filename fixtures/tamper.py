"""Corrupt a copy of a log: edit (qty+1 on a line), delete a line, or swap two lines."""

import argparse
import json

p = argparse.ArgumentParser()
p.add_argument("--in", dest="src", required=True)
p.add_argument("--out", required=True)
p.add_argument("--mode", choices=["edit", "delete", "swap"], required=True)
p.add_argument("--line", type=int, required=True, help="1-based line (= seq)")
a = p.parse_args()
lines = open(a.src, "rb").read().splitlines(keepends=True)
i = a.line - 1
if a.mode == "edit":
    d = json.loads(lines[i])
    d["qty"] += 1
    lines[i] = (json.dumps(d, sort_keys=True, separators=(",", ":")) + "\n").encode()
elif a.mode == "delete":
    del lines[i]
else:
    lines[i], lines[i + 1] = lines[i + 1], lines[i]
open(a.out, "wb").write(b"".join(lines))
print(f"{a.mode} on line {a.line}: wrote {a.out}")
