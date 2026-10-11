import pytest

from core.events import Order, Reason
from core.rules import check
from tests.helpers import RULES

NOW = 10**12


def verdict(side="BUY", sym="AAPL", qty=10, price=19000, pos=0, net=0, win=0, cd=0, rules=RULES):
    o = Order(client_order_id="x", side=side, symbol=sym, qty=qty, price=price)
    return check(rules, o, NOW, pos, net, win, cd)


@pytest.mark.parametrize("kwargs,code", [
    (dict(), Reason.OK),
    (dict(sym="TSLA"), Reason.BAD_ORDER),
    (dict(qty=0), Reason.BAD_ORDER),
    (dict(price=-5), Reason.BAD_ORDER),
    (dict(price=19950), Reason.OK),            # exactly +5%
    (dict(price=19951), Reason.PRICE_BAND),
    (dict(price=18050), Reason.OK),            # exactly -5%
    (dict(price=18049), Reason.PRICE_BAND),
    (dict(qty=100), Reason.OK),                # lands exactly on the limit
    (dict(qty=101), Reason.MAX_POSITION),
    (dict(qty=50, pos=60), Reason.MAX_POSITION),
    (dict(side="SELL", qty=200, pos=100), Reason.OK),   # flips to -100, not increasing
    (dict(side="SELL", qty=50, pos=100), Reason.OK),    # reducing
    (dict(win=600), Reason.RATE_LIMIT),
    (dict(win=599), Reason.OK),
    (dict(net=-5000), Reason.MAX_DAILY_LOSS),
    (dict(net=-4999), Reason.OK),
    (dict(net=-9999, side="SELL", pos=10, qty=10), Reason.OK),  # closing is always allowed
    (dict(cd=NOW + 1), Reason.LOSS_STREAK_COOLDOWN),
    (dict(cd=NOW), Reason.OK),
    (dict(cd=NOW + 1, side="SELL", pos=10, qty=10), Reason.OK),
])
def test_check(kwargs, code):
    v = verdict(**kwargs)
    assert v.code == code and v.accept == (code == Reason.OK)


def test_kill_switch_beats_everything():
    r = RULES.model_copy(update={"kill_switch": True})
    assert verdict(rules=r).code == Reason.KILL_SWITCH
    assert verdict(rules=r, side="SELL", pos=10, qty=10).code == Reason.KILL_SWITCH


def test_priority_first_failure_wins():
    assert verdict(qty=1000, price=30000).code == Reason.PRICE_BAND
    assert verdict(qty=1000, net=-5000, cd=NOW + 1).code == Reason.MAX_POSITION
    assert verdict(net=-5000, cd=NOW + 1).code == Reason.MAX_DAILY_LOSS
