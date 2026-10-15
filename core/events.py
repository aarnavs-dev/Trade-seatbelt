from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Side = Literal["BUY", "SELL"]
Decision = Literal["ACCEPT", "REJECT"]


class Reason:
    OK = "OK"
    BAD_ORDER = "BAD_ORDER"
    KILL_SWITCH = "KILL_SWITCH"
    RATE_LIMIT = "RATE_LIMIT"
    PRICE_BAND = "PRICE_BAND"
    MAX_POSITION = "MAX_POSITION"
    MAX_DAILY_LOSS = "MAX_DAILY_LOSS"
    LOSS_STREAK_COOLDOWN = "LOSS_STREAK_COOLDOWN"


class Order(BaseModel):
    """What a client sends. Strict: floats and wrong types are structural errors, not rejects."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    client_order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    symbol: str = Field(min_length=1, max_length=16)
    side: Side
    qty: int
    price: int  # minor units


class Event(Order):
    """One log line."""

    seq: int
    ts: int  # ns since epoch, stamped by the gate, never decreasing
    decision: Decision
    reason_code: str
    reason_text: str
    prev_hash: str
    hash: str

    def body(self) -> dict:
        """Everything the hash covers."""
        return self.model_dump(exclude={"hash", "prev_hash"})


LOG_VERSION = 2  # v1 logs hold order events only and record no rules; v2 logs also hold control records like the one below


class RulesRecord(BaseModel):
    """A control record: the gate wrote it at start-up to say which rules it was running. It is a normal chained line
    (it has a seq and takes part in prev_hash / hash), so editing it breaks the chain like any other edit. It is not an
    order: it has no decision and is never counted as one. `rules_hash` = core.rules.rules_hash(), the hash of the parsed rules."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    type: Literal["RULES"]
    seq: int
    ts: int
    log_version: int
    rules_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prev_hash: str
    hash: str

    def body(self) -> dict:
        return self.model_dump(exclude={"hash", "prev_hash"})


CONTROL_TYPES = {"RULES": RulesRecord}
