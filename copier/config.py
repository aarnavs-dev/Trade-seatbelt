"""Follower settings and the small validated override of rules limits. Nothing here touches the repo's rules.yaml."""

from dataclasses import asdict, dataclass

SCALAR_LIMITS = ("max_position_per_symbol", "max_daily_loss", "price_band_pct", "max_orders_per_minute")
COOLDOWN_KEYS = ("losses", "cooldown_seconds")


class ConfigError(ValueError):
    """A request the caller can fix; the message says what to change."""


def _pos_int(name, x, hi=None):
    if type(x) is not int or x <= 0 or (hi is not None and x > hi):
        raise ConfigError(f"{name} must be a positive integer" + (f" no larger than {hi}" if hi else "") + f", got {x!r}")
    return x


def validate_overrides(overrides) -> dict:
    """{'max_position_per_symbol': 50, 'loss_streak_cooldown': {'losses': 2}} -> same dict, or ConfigError.
    Unknown keys and non-positive / non-integer values are refused. kill_switch and fees cannot be overridden."""
    if not isinstance(overrides, dict):
        raise ConfigError("limit overrides must be a mapping")
    out = {}
    for k, v in overrides.items():
        if k in SCALAR_LIMITS:
            out[k] = _pos_int(k, v, 100 if k == "price_band_pct" else None)
        elif k == "loss_streak_cooldown":
            if not isinstance(v, dict) or not v:
                raise ConfigError("loss_streak_cooldown must be a non-empty mapping")
            bad = sorted(set(v) - set(COOLDOWN_KEYS))
            if bad:
                raise ConfigError(f"unknown key loss_streak_cooldown.{bad[0]} (allowed: {', '.join(COOLDOWN_KEYS)})")
            out[k] = {kk: _pos_int(f"loss_streak_cooldown.{kk}", vv) for kk, vv in v.items()}
        else:
            raise ConfigError(f"unknown limit {k!r} (allowed: {', '.join(SCALAR_LIMITS + ('loss_streak_cooldown',))})")
    return out


def parse_override_args(items) -> dict:
    """'max_position_per_symbol=50' / 'loss_streak_cooldown.losses=2' (repeatable CLI form) -> overrides dict."""
    out = {}
    for item in items or ():
        key, sep, val = item.partition("=")
        if not sep:
            raise ConfigError(f"override {item!r} must look like key=value")
        try:
            n = int(val)
        except ValueError:
            raise ConfigError(f"override {key} must be an integer, got {val!r}") from None
        head, dot, tail = key.partition(".")
        if dot:
            out.setdefault(head, {})[tail] = n
        else:
            out[key] = n
    return validate_overrides(out)


@dataclass(frozen=True)
class FollowerConfig:
    allowlist: tuple = ("local",)        # signers whose signals this follower trusts (the local feed's signer is "local")
    max_age_ms: int = 900_000            # a signal older than this on the replay clock is stale
    delay_ms: int = 0                    # modelled latency between the leader's decision and the follower's clock
    scale_num: int = 1                   # follower qty = leader qty * scale_num // scale_den (floor, integers only)
    scale_den: int = 2
    leader_drawdown_stop: int = 2_000    # cents; stop opening/adding when the leader's realized drawdown reaches this
    allow_short: bool = False            # False: a copied SELL is clipped to the follower's long position (never short)
    fixed_qty: int | None = None         # copy this many shares per trade instead of scaling the leader's qty
    max_trades: int | None = None        # session limits (copier pre-filters, counted on orders the gate ACCEPTED);
    max_volume: int | None = None        #   shares in total
    max_total_amount: int | None = None  #   cents in total
    max_trade_amount: int | None = None  #   cents per trade

    def __post_init__(self):
        if not self.allowlist or not all(isinstance(s, str) and s for s in self.allowlist):
            raise ConfigError("allowlist must be a non-empty list of signer names")
        _pos_int("max_age_ms", self.max_age_ms)
        if type(self.delay_ms) is not int or self.delay_ms < 0:
            raise ConfigError(f"delay_ms must be an integer >= 0, got {self.delay_ms!r}")
        _pos_int("scale_num", self.scale_num)
        _pos_int("scale_den", self.scale_den)
        _pos_int("leader_drawdown_stop", self.leader_drawdown_stop)
        for name in ("fixed_qty", "max_trades", "max_volume", "max_total_amount", "max_trade_amount"):
            if getattr(self, name) is not None:
                _pos_int(name, getattr(self, name))
        if type(self.allow_short) is not bool:
            raise ConfigError(f"allow_short must be true or false, got {self.allow_short!r}")

    def copy_description(self) -> dict:
        """How much is copied and the session limits, for the manifest."""
        size = ({"mode": "fixed", "shares_per_trade": self.fixed_qty} if self.fixed_qty
                else {"mode": "same" if (self.scale_num, self.scale_den) == (1, 1) else "percent", "scale": f"{self.scale_num}/{self.scale_den}"})
        return {**size, "session_limits": {"max_trades": self.max_trades, "max_volume": self.max_volume,
                                           "max_total_amount": self.max_total_amount, "max_trade_amount": self.max_trade_amount}}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["allowlist"] = sorted(self.allowlist)
        return d

    @classmethod
    def from_dict(cls, d):
        return cls(**{**d, "allowlist": tuple(d["allowlist"])})
