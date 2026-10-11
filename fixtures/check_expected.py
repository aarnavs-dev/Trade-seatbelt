"""Compare an auditor report.json with fixtures/expected.json. Exit 1 on any difference."""

import json
import sys

report, expected = (json.load(open(p)) for p in sys.argv[1:3])
actual = {
    "events": report["counts"]["events"], "accepted": report["counts"]["accepted"],
    "rejected": report["counts"]["rejected"],
    "rejects_by_reason": report["counts"]["rejects_by_reason"],
    "realized_gross": report["pnl"]["realized_gross"], "fees": report["pnl"]["fees"],
    "realized_net": report["pnl"]["realized_net"], "cash": report["cash"],
    "positions": report["positions"], "by_symbol": report["pnl"]["by_symbol"],
}
bad = [k for k in actual if actual[k] != expected[k]]
for k in bad:
    print(f"MISMATCH {k}: audited {actual[k]} expected {expected[k]}")
print("audited totals match the hand-computed expected totals" if not bad else "FAILED")
sys.exit(1 if bad else 0)
