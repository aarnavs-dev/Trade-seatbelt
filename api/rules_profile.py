"""Your rules: GET/PUT /rules. The saved profile is a small runtime file (gitignored, never the repo's rules.yaml). A follow
run uses it as its limit overrides; until it is saved a run is refused (409). Values are validated against the rules.yaml
schema: unknown keys and non-positive or non-integer values are refused with a message that names the key."""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

import orjson
import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from copier.config import ConfigError, validate_overrides
from copier.run import build_rules

LIMIT_KEYS = ("max_position_per_symbol", "max_daily_loss", "price_band_pct", "loss_streak_cooldown")
PRESETS = {      # money in cents
    "cautious": {"max_position_per_symbol": 25, "max_daily_loss": 2_500, "price_band_pct": 2, "loss_streak_cooldown": {"losses": 2, "cooldown_seconds": 600}},
    "balanced": {"max_position_per_symbol": 100, "max_daily_loss": 5_000, "price_band_pct": 5, "loss_streak_cooldown": {"losses": 3, "cooldown_seconds": 300}},
    "active": {"max_position_per_symbol": 250, "max_daily_loss": 20_000, "price_band_pct": 8, "loss_streak_cooldown": {"losses": 5, "cooldown_seconds": 120}},
}
MAX_BODY = 4096


def limits_hash(limits: dict) -> str:
    return hashlib.sha256(orjson.dumps(limits, option=orjson.OPT_SORT_KEYS)).hexdigest()


def read_profile(path: Path) -> dict | None:
    try:
        d = orjson.loads(Path(path).read_bytes())
        validate_overrides(d["limits"])
        return d if d["hash"] == limits_hash(d["limits"]) else None
    except (OSError, ValueError, KeyError, TypeError, ConfigError):
        return None


def validate_full(limits, base_rules) -> dict:
    """The four limits, all present, valid against the Rules schema. Raises ConfigError with a plain message."""
    if not isinstance(limits, dict):
        raise ConfigError("send an object with the four limits")
    ok = validate_overrides(limits)
    missing = [k for k in LIMIT_KEYS if k not in ok]
    if missing:
        raise ConfigError(f"missing {', '.join(missing)}")
    if set(ok["loss_streak_cooldown"]) != {"losses", "cooldown_seconds"}:
        raise ConfigError("loss_streak_cooldown needs both losses and cooldown_seconds")
    try:
        build_rules(base_rules, {"AAPL": 100}, ok)
    except ValidationError as e:
        raise ConfigError(f"{'.'.join(map(str, e.errors()[0]['loc']))}: {e.errors()[0]['msg']}") from None
    return ok


def build_router(profile_path: Path, base_rules: Path) -> APIRouter:
    r = APIRouter(prefix="/rules")
    profile_path, base_rules = Path(profile_path), Path(base_rules)

    def current():
        d = yaml.safe_load(base_rules.read_text())
        defaults = {k: d[k] for k in LIMIT_KEYS}
        prof = read_profile(profile_path)
        return {"saved": prof is not None, "limits": prof["limits"] if prof else defaults,
                "hash": prof["hash"] if prof else None, "saved_at": prof["saved_at"] if prof else None,
                "presets": PRESETS, "defaults": defaults, "simulated": True}

    @r.get("")
    def get_rules():
        return current()

    @r.put("")
    async def put_rules(request: Request):
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse({"error": "too_large", "message": "that request is too large"}, status_code=413)
        try:
            limits = validate_full(orjson.loads(body), base_rules)
        except orjson.JSONDecodeError:
            return JSONResponse({"error": "bad_json", "message": "the body is not valid JSON"}, status_code=422)
        except ConfigError as e:
            return JSONResponse({"error": "bad_limit", "message": str(e)}, status_code=422)
        prof = {"limits": limits, "hash": limits_hash(limits), "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = profile_path.with_name(profile_path.name + ".tmp")
        tmp.write_bytes(orjson.dumps(prof, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2) + b"\n")
        os.replace(tmp, profile_path)
        return current()

    return r
