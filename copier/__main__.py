"""python -m copier follow [...]   one follower over a leader's feed, then a short table
   python -m copier run --run-dir D --port P [--pace-ms N]   process D/feed.jsonl against a gate that is already running"""

import argparse
import sys
import orjson

from auditor.explain import explain_copier_log, explain_log, render
from copier import data as datamod
from copier.config import ConfigError, FollowerConfig, parse_override_args
from copier.follow import run_follower, write_status
from copier.run import ROOT, create_follow_run, start_gate, stop_gate
from core.money import fmt
from core.rules import load_rules
from leaders.publisher import Publisher
from leaders.runner import generate
from marketdata import SYMBOLS


def _table(rows, out):
    w = max(len(k) for k, _ in rows)
    for k, v in rows:
        out(f"  {k:<{w}}  {v}")


def _by(counter):
    return ", ".join(f"{k} {v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))) or "none"


def cmd_follow(a, out=print) -> int:
    try:
        overrides = parse_override_args(a.limit)
        num, _, den = a.scale.partition("/")
        cfg = FollowerConfig(allowlist=tuple(a.allow), max_age_ms=a.max_age_ms, delay_ms=a.delay_ms,
                             scale_num=int(num), scale_den=int(den or 1), leader_drawdown_stop=a.drawdown_stop)
    except (ConfigError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    data = datamod.load(a.symbols, offline=a.offline)
    run_dir = create_follow_run(a.runs_dir, base_rules=a.rules, data=data, cfg=cfg, overrides=overrides,
                                leader=a.leader, leader_qty=a.qty)
    pub = Publisher(run_dir)
    for sig in generate(data.bars, a.leader, data.provider, qty=a.qty):
        pub.publish(sig)
    proc = None
    try:
        write_status(run_dir, "running")
        proc, port = start_gate(run_dir)
        summary = run_follower(run_dir, port)
    except Exception:
        write_status(run_dir, "failed")
        raise
    finally:
        if proc is not None:
            stop_gate(proc)
    write_status(run_dir, "done", summary)

    rules = load_rules(run_dir / "rules.yaml")
    out(f"follow run {run_dir.name}   PAPER ONLY: fills are simulated at the order price")
    out(f"  SOURCE: {data.label}" + (f" ({data.provider}, session {data.session})" if not data.synthetic else
                                      " (generated bars, not market data)"))
    if data.fallback_reason:
        out(f"  real data not used: {data.fallback_reason}")
    out(f"  leader {a.leader} (qty {a.qty}), follower scale {a.scale}, allowlist {','.join(cfg.allowlist)}"
        + (f", limit overrides {overrides}" if overrides else ""))
    out(f"  dir {run_dir}")
    stopped = sum(summary["skipped"].values())
    rej = sum(summary["rejected"].values())
    _table([("signals seen", summary["seen"]), ("sent to the gate", summary["sent"]),
            ("accepted by the gate", summary["accepted"]),
            ("stopped by the copier", f"{stopped}  ({_by(summary['skipped'])})"),
            ("rejected by the rules", f"{rej}  ({_by(summary['rejected'])})")], out)
    exps = explain_log(run_dir / "gate.log", rules)
    seen, picks = set(), []
    for e in exps:
        if e["reason_code"] not in seen:
            seen.add(e["reason_code"])
            picks.append(e)
    picks = (picks + [e for e in exps if e not in picks])[:3]
    skips = explain_copier_log(run_dir / "copier.log")
    sample = (picks + skips)[:3]
    out(f"\n  {len(sample)} sample explanation(s) (deterministic, from the logs and rules.yaml; no AI). All of them: "
        f"python -m auditor.explain {run_dir.name}")
    for e in sample:
        out("\n" + render(e))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="copier")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("follow")
    f.add_argument("--leader", choices=("orb", "momentum", "meanrev"), default="momentum")
    f.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    f.add_argument("--qty", type=int, default=10, help="the leader's qty per signal")
    f.add_argument("--scale", default="1/2", help="follower qty = leader qty * num/den, rounded down")
    f.add_argument("--allow", nargs="+", default=["local"], help="trusted leaders (signers)")
    f.add_argument("--max-age-ms", type=int, default=900_000)
    f.add_argument("--delay-ms", type=int, default=0)
    f.add_argument("--drawdown-stop", type=int, default=2_000, help="cents of leader drawdown at which new risk is not copied")
    f.add_argument("--limit", action="append", default=[], metavar="KEY=INT", help="override a rules limit for this run, e.g. max_position_per_symbol=3")
    f.add_argument("--offline", action="store_true", help="skip the fetch and use the SYNTHETIC fixture")
    f.add_argument("--runs-dir", default=str(ROOT / "runs"))
    f.add_argument("--rules", default=str(ROOT / "rules.yaml"))
    r = sub.add_parser("run")
    r.add_argument("--run-dir", required=True)
    r.add_argument("--port", type=int, required=True)
    r.add_argument("--pace-ms", type=int, default=0)
    a = p.parse_args(argv)
    if a.cmd == "follow":
        return cmd_follow(a)
    print(orjson.dumps(run_follower(a.run_dir, a.port, pace_ms=a.pace_ms)).decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
