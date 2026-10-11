"""python -m copier follow [...]   one follower over a leader's feed, then a short table
   python -m copier run --run-dir D --port P [--pace-ms N]   process D/feed.jsonl against a gate that is already running"""

import argparse
import sys
import os

import orjson

from auditor.explain import explain_copier_log, explain_log, render
from pathlib import Path

from copier import data as datamod
from copier.config import ConfigError, FollowerConfig, parse_override_args
from copier.feed import write_feed
from copier.pace import NAMES as PACE_NAMES, Pace
from copier.follow import run_follower, write_status
from copier.run import ROOT, create_follow_run, start_gate, stop_gate
from core.money import fmt
from core.rules import load_rules
from leaders.__main__ import load_config
from leaders.feed import ChainFeed
from leaders.publisher import Publisher
from leaders.runner import generate
from leaders.solana import Keypair, RpcClient
from marketdata.calendar import ny_date
from marketdata import SYMBOLS


def _table(rows, out):
    w = max(len(k) for k, _ in rows)
    for k, v in rows:
        out(f"  {k:<{w}}  {v}")


def _by(counter):
    return ", ".join(f"{k} {v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))) or "none"


def read_chain(a, out=print):
    """-> (items, feed_info, allowlist). items is None when the chain AND its cache are unavailable (use the local feed)."""
    cfg = load_config()
    try:
        address = a.leader_address or Keypair.load(cfg["keypair"]).address      # public key only; the secret is never printed
    except ValueError as e:
        out(f"  chain feed unavailable: {e}; run `make leaders-setup` or pass --leader-address")
        return None, {"source": "local feed", "badge": "local feed", "requested": "chain", "why": "no leader public key"}, None
    cache = Path("marketdata/cache") / f"chain-{address[:8]}.json"
    res = ChainFeed(RpcClient(cfg["rpc"]), [address], cache).read()
    info = {"badge": "local feed", "requested": "chain", "leader": address, "rpc_host": cfg["rpc"].split("/")[2], "notes": res.notes,
            "rejected_by_reader": len(res.rejected)}
    if res.source == "chain" and res.items:
        return res.items, {**info, "source": "chain", "badge": "chain (devnet)"}, (address,)
    if res.source == "cache" and res.items:
        return res.items, {**info, "source": "chain cache (stale)", "badge": "chain (devnet, cached)"}, (address,)
    why = "; ".join(res.notes) if res.source != "chain" else "the leader has published no signals on devnet yet"
    return None, {**info, "source": "local feed", "why": why[:200]}, None      # local signer is "local": keep that allowlist


def cmd_follow(a, out=print) -> int:
    chain_items, feed_info, allow = None, {"source": "local feed", "badge": "local feed"}, tuple(a.allow)
    if a.announce:      # the API runs this as a subprocess: stop cleanly (stop the gate) when it is terminated
        import signal as _signal
        _signal.signal(_signal.SIGTERM, lambda *_: sys.exit(143))
    if a.feed in ("chain", "auto"):
        chain_items, feed_info, chain_allow = read_chain(a, out)
        allow = chain_allow or allow
        if a.feed == "chain" and chain_items is None:       # strict: no silent fallback
            print(f"error: the chain feed is unavailable ({feed_info.get('why')}); nothing was started", file=sys.stderr)
            return 3
    try:
        overrides = parse_override_args(a.limit)
        num, _, den = a.scale.partition("/")
        cfg = FollowerConfig(allowlist=allow, max_age_ms=a.max_age_ms, delay_ms=a.delay_ms,
                             scale_num=int(num), scale_den=int(den or 1), leader_drawdown_stop=a.drawdown_stop, allow_short=a.allow_short, fixed_qty=a.fixed_qty,
                             max_trades=a.max_trades, max_volume=a.max_volume, max_total_amount=a.max_total_amount,
                             max_trade_amount=a.max_trade_amount)
    except (ConfigError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    pace = None if a.pace == "off" else Pace(a.pace, a.pace_seed if a.pace_seed is not None else int.from_bytes(os.urandom(6), "big"))
    day = ny_date(min(i.signal.data_ts for i in chain_items) - 1) if chain_items else None
    data = datamod.load(a.symbols, offline=a.offline, day=day)
    run_dir = create_follow_run(a.runs_dir, base_rules=a.rules, data=data, cfg=cfg, overrides=overrides,
                                leader=a.leader, leader_qty=a.qty, feed=feed_info, pace=pace.to_dict() if pace else None)
    if a.announce:
        print(f"RUN {run_dir.name}", flush=True)
    if chain_items:
        write_feed(run_dir / "feed.jsonl", chain_items)
    else:   # local feed: the leader strategy replayed over the same bars
        pub = Publisher(run_dir)
        for sig in generate(data.bars, a.leader, data.provider, qty=a.qty):
            pub.publish(sig)
    proc = None
    try:
        write_status(run_dir, "running")
        proc, port = start_gate(run_dir)
        summary = run_follower(run_dir, port, pace_ms=a.pace_ms)
    except SystemExit:
        write_status(run_dir, "stopped")
        raise
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
    out(f"  FEED: source: {feed_info['source']}" + (f"  (asked for chain: {feed_info['why']})" if feed_info.get("why") else "")
        + (f"  leader {feed_info['leader']}" if feed_info.get("leader") else ""))
    if chain_items and str(day) != data.session:
        out(f"  WARNING: the chain signals are from {day} but the price anchors are from {data.session}; expect PRICE_BAND rejects")
    out(f"  leader {a.leader} (qty {a.qty}), follower scale {a.scale}, allowlist {','.join(cfg.allowlist)}"
        + (f", limit overrides {overrides}" if overrides else ""))
    out(f"  dir {run_dir}")
    stopped = sum(summary["skipped"].values())
    rej = sum(summary["rejected"].values())
    _table([("signals seen", summary["seen"]), ("sent to the gate", summary["sent"]),
            ("accepted by the gate", summary["accepted"]),
            ("clipped to long position", summary["clipped"]),
            ("stopped by the copier", f"{stopped}  ({_by(summary['skipped'])})"),
            ("rejected by the rules", f"{rej}  ({_by(summary['rejected'])})")], out)
    if summary.get("session_end"):
        out(f"  {summary['session_end']['text']}  The session finished here.")
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
    f.add_argument("--feed", choices=("local", "chain", "auto"), default="local",
                   help="chain: the leader's signals from devnet, or fail; auto: devnet, falling back to the cache and then a local feed (and saying so)")
    f.add_argument("--fixed-qty", type=int, help="copy this many shares per trade instead of scaling")
    f.add_argument("--max-trades", type=int, help="session limit: trades the gate accepted")
    f.add_argument("--max-volume", type=int, help="session limit: total shares")
    f.add_argument("--max-total-amount", type=int, help="session limit: total cents")
    f.add_argument("--max-trade-amount", type=int, help="limit per trade, cents")
    f.add_argument("--pace", choices=PACE_NAMES, default="off", help="seeded wait between signals; changes timing only")
    f.add_argument("--pace-seed", type=int)
    f.add_argument("--leader-address", help="the leader's public key for --feed chain (default: from LEADER_KEYPAIR_PATH)")
    f.add_argument("--announce", action="store_true", help="print 'RUN <id>' once the run exists (used by the API)")
    f.add_argument("--pace-ms", type=int, default=0, help="sleep between signals so a UI can show the feed live")
    f.add_argument("--allow-short", action="store_true", help="let a copied SELL open a short (default: close-only)")
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
