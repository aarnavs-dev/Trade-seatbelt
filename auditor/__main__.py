import argparse
import os

from auditor.fills_csv import CsvError, parse_fills_csv
from auditor.recon import DEFAULT_TOLERANCE_S, reconcile
from auditor.replay import audit
from auditor.report import build_report, to_json, to_markdown
from core.rules import load_rules

if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="auditor")
    p.add_argument("--log", required=True)
    p.add_argument("--rules", default="rules.yaml")
    p.add_argument("--out", help="directory for report.json and report.md")
    p.add_argument("--fills-csv", help="broker fills CSV to reconcile the log's ACCEPTED fills against")
    p.add_argument("--tolerance-seconds", type=int, default=DEFAULT_TOLERANCE_S,
                   help="max |ts difference| for matching rows that have no client_order_id (default %(default)s)")
    p.add_argument("--lenient", action="store_true",
                   help="with --fills-csv: skip and report every bad CSV row instead of failing on the first")
    args = p.parse_args()
    if args.tolerance_seconds < 0:
        p.error("--tolerance-seconds must be >= 0")
    parsed = None
    if args.fills_csv:
        try:
            parsed = parse_fills_csv(args.fills_csv, lenient=args.lenient)
        except CsvError as e:
            print(f"FILLS CSV REJECTED, nothing written: {e}")
            raise SystemExit(4)
    a = audit(args.log, load_rules(args.rules))
    recon = None
    if parsed is not None:
        if a.chain_error is not None:
            recon = {"status": "not_run", "reason": f"log chain is broken at seq {a.chain_error.seq}; "
                     "reconciling against an unverified log would prove nothing"}
        else:
            recon = reconcile(a.fills, parsed.fills, args.tolerance_seconds, a.rejected)
            recon["csv"] = args.fills_csv
            recon["rejected_rows"] = [{"row": e.row, "message": e.message} for e in parsed.errors]
    r = build_report(a, args.log, recon)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "report.json"), "wb") as f:
            f.write(to_json(r))
        with open(os.path.join(args.out, "report.md"), "w") as f:
            f.write(to_markdown(r))
    ch = r["chain"]
    if not ch["ok"]:
        print(f"CHAIN BROKEN at seq {ch['first_bad_seq']}: {ch['reason']}")
        raise SystemExit(2)
    rr = r["rules_record"]
    msg = (f"chain OK, {ch['events_verified']} events, {len(r['findings'])} findings, rules {rr['status']}"
           f"{' (log does not record its rules)' if rr['status'] == 'NOT_RECORDED' else ''}")
    unmatched = 0
    if recon:
        c = recon["counts"]
        unmatched = sum(v for k, v in c.items() if k != "MATCHED") + len(recon["rejected_rows"])
        msg += ", reconciliation: " + ", ".join(f"{k} {v}" for k, v in c.items())
    print(msg)
    raise SystemExit(3 if r["findings"] or unmatched else 0)
