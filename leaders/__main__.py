"""python -m leaders setup | run. Config comes from the environment or .env (SOLANA_RPC_URL, LEADER_KEYPAIR_PATH)."""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import orjson

from leaders.publisher import Publisher
from leaders.runner import generate
from leaders.solana import DEFAULT_RPC, Keypair, MainnetWriteRefused, RpcClient, RpcError, assert_devnet
from marketdata import SYMBOLS, MarketDataError
from marketdata.providers import choose_provider
from marketdata.replay import ReplayBackend

WANTED = ("SOLANA_RPC_URL", "LEADER_KEYPAIR_PATH", "ALPACA_KEY_ID", "ALPACA_SECRET_KEY")


def load_config(env=None, dotenv=".env"):
    env = os.environ if env is None else env
    file = {}
    try:
        for line in Path(dotenv).read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() in WANTED:
                file[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    get = lambda k, d="": env.get(k) or file.get(k) or d
    return {"rpc": get("SOLANA_RPC_URL", DEFAULT_RPC), "keypair": get("LEADER_KEYPAIR_PATH", ".keys/leader.json")}


def cmd_setup(cfg, rpc=None, out=print) -> int:
    rpc = rpc or RpcClient(cfg["rpc"])
    path = Path(cfg["keypair"])
    try:
        assert_devnet(rpc)
    except MainnetWriteRefused as e:
        out(f"leaders-setup: {e}")
        return 2
    except RpcError as e:
        out(f"leaders-setup: cannot reach the devnet RPC ({e}). Nothing was created. Check your network or SOLANA_RPC_URL.")
        return 1
    if path.exists():
        try:
            kp = Keypair.load(path)
        except ValueError as e:
            out(f"leaders-setup: {e}")
            return 1
        out(f"keypair already exists at {path}")
    else:
        kp = Keypair.generate()
        kp.save(path)
        out(f"created throwaway devnet keypair at {path} (mode 600, gitignored)")
    out(f"public address: {kp.address}")
    try:
        rpc.call("requestAirdrop", [kp.address, 1_000_000_000])
        out("airdrop of 1 devnet SOL requested")
    except RpcError as e:
        out(f"leaders-setup: airdrop failed ({e}). The devnet faucet is often rate-limited: try again later or use "
            f"https://faucet.solana.com with the address above. Local feeds (runs/<id>/feed.jsonl) work without it.")
        return 1
    return 0


EXPLORER = "https://explorer.solana.com/tx/{}?cluster=devnet"
CHECK_SEED, CHECK_N, MIN_LAMPORTS = 20261011, 10, 100_000


def check_signals(first_seq: int):
    """10 seeded signals (same content every time; only leader_seq starts after what is already on chain)."""
    import random
    from leaders.signal import Signal
    rng, base = random.Random(CHECK_SEED), 1_790_000_000_000
    out = []
    for i in range(CHECK_N):
        sym = rng.choice(SYMBOLS)
        out.append(Signal(first_seq + i, sym, "BUY" if i % 2 == 0 else "SELL", rng.randint(1, 50), rng.randint(10_000, 60_000),
                          base + i * 300_000, "devnet-check", base + i * 300_000))
    return out


def cmd_devnet_check(cfg, out=print, sleep=None, rpc=None) -> int:
    """Publish 10 seeded signals, read them back with the chain reader and compare with feed.jsonl. Devnet only."""
    import time
    from leaders.feed import ChainFeed
    sleep = sleep or time.sleep
    try:
        kp = Keypair.load(cfg["keypair"])
    except ValueError as e:
        out(f"devnet-check: {e}; run `make leaders-setup` first")
        return 1
    rpc = rpc or RpcClient(cfg["rpc"])
    try:
        assert_devnet(rpc)
        lamports = rpc.call("getBalance", [kp.address])["value"]
    except MainnetWriteRefused as e:
        out(f"devnet-check: {e}")
        return 2
    except RpcError as e:
        out(f"devnet-check: cannot reach the devnet RPC ({e}). Nothing was published.")
        return 1
    out(f"leader {kp.address}  balance {lamports} lamports (devnet)")
    if lamports < MIN_LAMPORTS:
        out("devnet-check: the keypair is not funded. Run `make leaders-setup` again later or use https://faucet.solana.com "
            f"with the address above. Nothing was published.")
        return 1
    cache = Path("out/devnet-check/chain-cache.json")
    reader = ChainFeed(rpc, [kp.address], cache)
    try:
        before = reader.read()
    except RpcError as e:
        out(f"devnet-check: cannot read the chain ({e})")
        return 1
    if before.source != "chain":
        out(f"devnet-check: could not read the chain ({'; '.join(before.notes)})")
        return 1
    first = max([i.signal.leader_seq for i in before.items], default=0) + 1
    run_dir = Path("out/devnet-check") / f"{first:08d}"
    pub = Publisher(run_dir, rpc, kp)
    sent = []
    for sig in check_signals(first):
        line = pub.publish(sig)
        if not pub.chain_ok:
            out("devnet-check: the real node did not take the transaction. Exact error from the publisher:")
            out(f"  {pub.disabled_reason}")
            return 1
        sent.append(line["tx"])
    out(f"published {len(sent)} signals (leader_seq {first}..{first + len(sent) - 1}); reading them back")
    deadline, got = time.monotonic() + 120, []
    while time.monotonic() < deadline:
        try:
            res = reader.read()
        except RpcError:
            res = None
        if res is not None and res.source == "chain":
            got = [i for i in res.items if i.signature in set(sent)]
            if len(got) == len(sent):
                break
        sleep(10)
    expected = [orjson.loads(l)["signal"] for l in (run_dir / "feed.jsonl").read_bytes().splitlines()]
    for n, tx in enumerate(sent, 1):
        out(f"  {n:2d}. {EXPLORER.format(tx)}")
    if [i.signal.to_dict() for i in got] != expected or [i.signature for i in got] != sent:
        out(f"devnet-check: MISMATCH. published {len(sent)}, read back {len(got)}; "
            f"in order and content they differ from {run_dir / 'feed.jsonl'}")
        return 1
    out(f"devnet-check: OK. {len(got)} signals read back from devnet equal feed.jsonl in order and content "
        f"(slots {got[0].slot}..{got[-1].slot}).")
    return 0


def cmd_run(args, cfg, out=print) -> int:
    provider = choose_provider(dotenv_path=".env")
    md = ReplayBackend(provider)
    try:
        bs = md.get_bars(args.symbols, date.fromisoformat(args.start), date.fromisoformat(args.end), args.interval)
    except MarketDataError as e:
        out(f"no data: {e}")
        return 1
    from sim.runfiles import new_run_id
    run_dir = Path(args.runs_dir) / new_run_id()
    rpc = kp = None
    if args.chain:
        try:
            kp = Keypair.load(cfg["keypair"])
        except ValueError as e:
            out(f"{e}; run `make leaders-setup` first, or drop --chain for a local-only feed")
            return 1
        rpc = RpcClient(cfg["rpc"])
    try:
        pub = Publisher(run_dir, rpc, kp)
        n = 0
        for sig in generate(bs.bars, args.leader, bs.provider, qty=args.qty):
            pub.publish(sig)
            n += 1
    except MainnetWriteRefused as e:
        out(f"refused: {e}")
        return 2
    out(f"{n} signals from {len(bs.bars)} {bs.provider} bars ({'cache' if bs.from_cache else 'fresh'}) -> {pub.path}")
    out("chain: " + ("published to devnet" if pub.chain_ok else f"LOCAL FEED ONLY ({pub.disabled_reason})"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="leaders")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("devnet-check")
    r = sub.add_parser("run")
    r.add_argument("--leader", choices=("orb", "momentum", "meanrev"), default="orb")
    r.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    r.add_argument("--start", required=True)
    r.add_argument("--end", required=True)
    r.add_argument("--interval", default="5m")
    r.add_argument("--qty", type=int, default=10)
    r.add_argument("--chain", action="store_true", help="also publish to devnet (default: local feed only)")
    r.add_argument("--runs-dir", default="runs")
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.cmd == "devnet-check":
        return cmd_devnet_check(cfg)
    return cmd_setup(cfg) if args.cmd == "setup" else cmd_run(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
