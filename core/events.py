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
