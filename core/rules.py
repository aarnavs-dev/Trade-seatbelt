"""Rules schema and the pure check() function. No I/O, no clock, no hidden state."""

from typing import Literal, NamedTuple

import yaml
from pydantic import BaseModel, ConfigDict, Field

from core.events import Reason

_M = ConfigDict(strict=True, frozen=True, extra="forbid")


class SymbolCfg(BaseModel):
    model_config = _M
    reference_price: int = Field(gt=0)


class Cooldown(BaseModel):
    model_config = _M
    losses: int = Field(gt=0)
    cooldown_seconds: int = Field(gt=0)


class Fees(BaseModel):
    model_config = _M
    per_share_minor: int = Field(ge=0)
    min_per_order_minor: int = Field(ge=0)

    def of(self, qty: int) -> int:
        return max(qty * self.per_share_minor, self.min_per_order_minor)


class Rules(BaseModel):
    model_config = _M
    version: Literal[1]
    symbols: dict[str, SymbolCfg]
    max_position_per_symbol: int = Field(gt=0)
    max_daily_loss: int = Field(gt=0)
    price_band_pct: int = Field(ge=0, le=100)
    max_orders_per_minute: int = Field(gt=0)
    loss_streak_cooldown: Cooldown
    kill_switch: bool = False
    fees: Fees


def load_rules(path) -> Rules:
    with open(path) as f:
        return Rules.model_validate(yaml.safe_load(f))


class Verdict(NamedTuple):
    accept: bool
    code: str
    text: str


def check(rules: Rules, order, ts: int, position: int, realized_net: int,
          orders_in_window: int, cooldown_until: int) -> Verdict:
    """Decide one order. Order of checks is part of the contract (first failure wins).

    Risk-increasing means the order grows |position|. MAX_DAILY_LOSS and the cooldown only block
    those, so a trader is never trapped in a position they are trying to close.
    """
    cfg = rules.symbols.get(order.symbol)
    if cfg is None:
        return Verdict(False, Reason.BAD_ORDER, f"unknown symbol {order.symbol}")
    if order.qty <= 0:
        return Verdict(False, Reason.BAD_ORDER, f"qty {order.qty} must be positive")
    if order.price <= 0:
        return Verdict(False, Reason.BAD_ORDER, f"price {order.price} must be positive")
    if rules.kill_switch:
        return Verdict(False, Reason.KILL_SWITCH, "kill switch is on, all orders halted")
    if orders_in_window >= rules.max_orders_per_minute:
        return Verdict(False, Reason.RATE_LIMIT,
                       f"{orders_in_window} orders in the last 60s, limit {rules.max_orders_per_minute}")
    ref, band = cfg.reference_price, rules.price_band_pct
    if abs(order.price - ref) * 100 > ref * band:
        return Verdict(False, Reason.PRICE_BAND,
                       f"price {order.price} is more than {band}% from reference {ref}")
    new_pos = position + (order.qty if order.side == "BUY" else -order.qty)
    increasing = abs(new_pos) > abs(position)
    limit = rules.max_position_per_symbol
    if increasing and abs(new_pos) > limit:
        return Verdict(False, Reason.MAX_POSITION,
                       f"{order.symbol} position would be {new_pos}, limit is {limit}")
    if increasing and realized_net <= -rules.max_daily_loss:
        return Verdict(False, Reason.MAX_DAILY_LOSS,
                       f"realized loss {-realized_net} reached daily limit {rules.max_daily_loss}")
    if increasing and ts < cooldown_until:
        return Verdict(False, Reason.LOSS_STREAK_COOLDOWN,
                       f"cooling down after {rules.loss_streak_cooldown.losses} losing trades in a row")
    return Verdict(True, Reason.OK, "within all limits")
