"""A run is one directory runs/<run_id>/. This module creates and reads it; only the simulator process writes
sim.status.json, only the API writes control.json, and only the gate ever writes gate.log."""

import hashlib
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import orjson
import yaml

from core.rules import Rules
from sim import DEFAULT_MAX_ORDERS, DEFAULT_SPEED, INTERVALS, MAX_SPEED, MAX_SYMBOLS, MIN_SPEED, PROFILES, PROVIDERS, VERSION
from sim.providers import DEFAULT_SYMBOLS, Fetched, ProviderError, Quotes, check_symbols, fetch_with_fallback

RUN_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")
SEED_MAX = 2 ** 64


class RunError(Exception):
    """A request the caller can fix (bad profile, unknown symbol, ...)."""


def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(orjson.dumps(obj, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n")
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        return orjson.loads(path.read_bytes())
    except (OSError, orjson.JSONDecodeError):
        return None


def new_run_id(now: float | None = None) -> str:
    t = datetime.fromtimestamp(time.time() if now is None else now, timezone.utc)
    return f"{t:%Y%m%d-%H%M%S}-{os.urandom(2).hex()}"


def run_dir_for(runs_dir: Path, run_id: str) -> Path:
    """The only way a run id becomes a path. Anything but the generated shape is refused, so '..', '/' etc. never get through."""
    if not isinstance(run_id, str) or not RUN_ID_RE.match(run_id):
        raise RunError("bad run id")
    return Path(runs_dir) / run_id


def git_info(root: Path) -> dict:
    def git(*a):
        return subprocess.run(["git", *a], cwd=root, capture_output=True, text=True, timeout=5).stdout.strip()
    try:
        return {"commit": git("rev-parse", "HEAD") or "unknown", "dirty": bool(git("status", "--porcelain", "--", "."))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": None}


def default_seed() -> int:
    return int.from_bytes(os.urandom(8), "big")


def validate(profile, seed, provider, symbols, interval, speed, max_orders):
    if profile not in PROFILES:
        raise RunError(f"profile must be one of {', '.join(PROFILES)}")
    if provider not in PROVIDERS:
        raise RunError(f"provider must be one of {', '.join(PROVIDERS)}")
    if interval not in INTERVALS:
        raise RunError(f"interval must be one of {', '.join(INTERVALS)}")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < SEED_MAX):
        raise RunError("seed must be an integer from 0 to 2^64-1")
    if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not MIN_SPEED <= speed <= MAX_SPEED:
        raise RunError(f"speed must be between {MIN_SPEED:g} and {MAX_SPEED:g} orders per second")
    if not isinstance(max_orders, int) or isinstance(max_orders, bool) or not 1 <= max_orders <= 5_000_000:
        raise RunError("max_orders must be between 1 and 5,000,000")
    try:
        symbols = check_symbols(symbols or DEFAULT_SYMBOLS)
    except ProviderError as e:
        raise RunError(str(e)) from None
    if len(symbols) > MAX_SYMBOLS:
        raise RunError(f"at most {MAX_SYMBOLS} symbols")
    return symbols


def run_rules(base_rules_path: Path, anchors: dict) -> tuple[str, Rules]:
    """The repo's rules.yaml limits, unchanged, with `symbols` replaced by this run's snapshot anchors."""
    d = yaml.safe_load(Path(base_rules_path).read_text())
    d["symbols"] = {s: {"reference_price": c} for s, c in sorted(anchors.items())}
    rules = Rules.model_validate(d)
    text = ("# Generated for this run from rules.yaml: every limit is copied unchanged; only `symbols` comes from\n"
            "# market_snapshot.json (reference_price = snapshot price in cents).\n" + yaml.safe_dump(d, sort_keys=False))
    return text, rules


def create_run(runs_dir, *, base_rules, root, profile="disciplined", seed=None, provider="auto", symbols=None,
               interval="1d", speed=DEFAULT_SPEED, max_orders=DEFAULT_MAX_ORDERS, snapshot_file=None,
               fsync="batched", env=None, providers=None, now=None) -> Path:
    """Fetch the snapshot once, write the run directory (manifest, snapshot, rules) and return it. Starts nothing."""
    symbols = validate(profile, seed, provider, symbols, interval, speed, max_orders)
    seed = default_seed() if seed is None else seed
    root, runs_dir = Path(root), Path(runs_dir)

    if snapshot_file is not None:   # CLI replay of an earlier run's prices; never reachable from the API
        snap = read_json(Path(snapshot_file))
        if not snap or "anchors_minor" not in snap:
            raise RunError("snapshot file is unreadable")
        missing = [s for s in symbols if s not in snap["anchors_minor"]]
        if missing:
            raise RunError(f"snapshot has no price for {', '.join(missing)}")
        q = Quotes(snap["provider"], {s: int(snap["anchors_minor"][s]) for s in symbols}, snap["interval"],
                   snap["timezone"], snap.get("note", ""))
        fetched = Fetched(q, snap.get("requested_provider", provider), snap.get("fallback_reason"), snap.get("attempts", []))
        retrieved_at = snap["retrieved_at"]
        reused = True
    else:
        try:
            fetched = fetch_with_fallback(provider, symbols, interval, env=env, dotenv_path=root / ".env", providers=providers)
        except ProviderError as e:
            raise RunError(f"no price source could supply these symbols: {e}") from None
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        reused = False
    q = fetched.quotes

    rules_text, _ = run_rules(base_rules, q.anchors)
    run_id = new_run_id(now)
    d = run_dir_for(runs_dir, run_id)
    d.mkdir(parents=True, exist_ok=False)
    snapshot = {"provider": q.provider, "requested_provider": fetched.requested, "fallback_reason": fetched.fallback_reason,
                "attempts": fetched.attempts, "symbols": list(q.anchors), "interval": q.interval, "timezone": q.timezone,
                "retrieved_at": retrieved_at, "reused_from_file": reused, "note": q.note,
                "anchors_minor": q.anchors,
                "disclaimer": "Prices are used only to seed a simulation. No order is ever sent to any broker."}
    write_json_atomic(d / "market_snapshot.json", snapshot)
    (d / "rules.yaml").write_text(rules_text)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest = {
        "run_id": run_id, "simulated": True, "sim_version": VERSION, "started_at": started,
        "seed": seed, "profile": profile, "speed": speed, "max_orders": max_orders, "fsync": fsync,
        "provider": q.provider, "requested_provider": fetched.requested, "fallback_reason": fetched.fallback_reason,
        "symbols": list(q.anchors), "interval": q.interval, "timezone": q.timezone,
        "rules_sha256": sha256_file(d / "rules.yaml"), "base_rules_sha256": sha256_file(base_rules),
        "snapshot_sha256": sha256_file(d / "market_snapshot.json"),
        "position_limit": run_rules(base_rules, q.anchors)[1].max_position_per_symbol,
        "git": git_info(root),
    }
    write_json_atomic(d / "manifest.json", manifest)
    return d
