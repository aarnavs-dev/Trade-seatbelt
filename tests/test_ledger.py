from hypothesis import given, strategies as st

from core.ledger import Ledger


def test_fifo_long_then_short_flip():
    l = Ledger()
    l.fill("X", "BUY", 10, 100, 5)
    l.fill("X", "BUY", 10, 110, 5)
    g, closed = l.fill("X", "SELL", 25, 120, 5)  # closes 10@100, 10@110, opens short 5@120
    assert (g, closed) == (10 * 20 + 10 * 10, 20)
    assert l.position("X") == -5 and l.lots["X"][0] == [-5, 120]
    g, closed = l.fill("X", "BUY", 5, 115, 5)     # short 5@120 covered at 115
    assert (g, closed) == (25, 5) and l.position("X") == 0
    assert l.realized_gross == 325 and l.fees == 20 and l.identity_ok()


fills = st.lists(st.tuples(st.sampled_from(["A", "B"]), st.sampled_from(["BUY", "SELL"]),
                           st.integers(1, 50), st.integers(1, 1000), st.integers(0, 100)),
                 max_size=80)


@given(fills)
def test_identity_and_positions_after_every_fill(seq):
    l, pos = Ledger(), {}
    for sym, side, qty, price, fee in seq:
        l.fill(sym, side, qty, price, fee)
        pos[sym] = pos.get(sym, 0) + (qty if side == "BUY" else -qty)
        assert l.identity_ok()
        assert l.position(sym) == pos[sym] == sum(q for q, _ in l.lots[sym])
        assert all(isinstance(v, int) for v in (l.cash, l.realized_gross, l.fees))
