import argparse
import os

from auditor.replay import audit
from auditor.report import build_report, to_json, to_markdown
from core.rules import load_rules

if __name__ == "__main__":
    p = argparse.ArgumentParser(prog="auditor")
    p.add_argument("--log", required=True)
    p.add_argument("--rules", default="rules.yaml")
    p.add_argument("--out", help="directory for report.json and report.md")
    args = p.parse_args()
    a = audit(args.log, load_rules(args.rules))
    r = build_report(a, args.log)
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
    print(f"chain OK, {ch['events_verified']} events, {len(r['findings'])} findings")
    raise SystemExit(3 if r["findings"] else 0)
