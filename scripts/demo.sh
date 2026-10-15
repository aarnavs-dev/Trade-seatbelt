#!/usr/bin/env bash
# client -> gate -> log -> auditor -> report, then a tamper check.
set -euo pipefail
PY=.venv/bin/python
OUT=out/demo
PORT=${PORT:-9101}
rm -rf "$OUT" && mkdir -p "$OUT"

echo "== 1. generate fixture (seed 7)"
$PY fixtures/gen_fixtures.py --seed 7 --out fixtures

echo; echo "== 2. start gate (fsync=batched)"
$PY -m gate --rules rules.yaml --log "$OUT/gate.log" --port "$PORT" --fsync batched > "$OUT/gate.out" 2>&1 &
GATE=$!
trap 'kill $GATE 2>/dev/null || true' EXIT

echo; echo "== 3. client sends fixture orders"
$PY -m client --fixture fixtures/orders.jsonl --port "$PORT"
kill -TERM $GATE; wait $GATE || true
cat "$OUT/gate.out"

echo; echo "== 4. audit the log"
$PY -m auditor --log "$OUT/gate.log" --rules rules.yaml --out "$OUT" || true
cat "$OUT/report.md"

echo "== 5. compare with hand-computed expected totals"
$PY fixtures/check_expected.py "$OUT/report.json" fixtures/expected.json

echo; echo "== 6. tamper checks (copies of the log)"
for spec in "edit 5" "delete 9" "swap 12" "rules 1"; do
  set -- $spec
  $PY fixtures/tamper.py --in "$OUT/gate.log" --out "$OUT/tampered_$1.log" --mode "$1" --line "$2"
  $PY -m auditor --log "$OUT/tampered_$1.log" --rules rules.yaml --out "$OUT/tampered_$1" || true
done
