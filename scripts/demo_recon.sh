#!/usr/bin/env bash
# Reconciliation + counterfactual on the generated fixtures. No network, no wall clock: the fixture log is a real
# hash-chained log written by the real gate with a scripted clock.
set -euo pipefail
PY=.venv/bin/python
OUT=out/recon
rm -rf "$OUT" && mkdir -p "$OUT"

$PY fixtures/gen_recon_fixtures.py --out fixtures/recon > /dev/null
# exit 3 = "differences found", which is the expected result on this fixture
$PY -m auditor --log fixtures/recon/gate_log.jsonl --rules rules.yaml --fills-csv fixtures/recon/broker_fills.csv \
  --out "$OUT" > "$OUT/auditor.out" || [ $? -eq 3 ]
$PY -m auditor.counterfactual --csv fixtures/recon/cf_trades.csv --rules rules.yaml --out "$OUT" > "$OUT/cf.out"
$PY fixtures/check_recon_expected.py "$OUT/report.json" "$OUT/counterfactual.json" fixtures/recon

# a broker CSV with a float price on row 7 must be refused, naming the row
sed '7s/,42100,/,421.00,/' fixtures/recon/broker_fills.csv > "$OUT/bad_broker.csv"
echo "BAD INPUT       $($PY -m auditor --log fixtures/recon/gate_log.jsonl --fills-csv "$OUT/bad_broker.csv" --out "$OUT/bad" || true)"
echo
echo "files: $OUT/report.md (reconciliation section), $OUT/report.json, $OUT/counterfactual.md, $OUT/counterfactual.json"
