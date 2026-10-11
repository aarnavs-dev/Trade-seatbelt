"""Disabled backends. Every method raises NotEnabledError; none of them imports a network library or opens a socket."""

from marketdata import NotEnabledError


class _Disabled:
    name = "disabled"

    def get_bars(self, *a, **k):
        raise NotEnabledError(f"{self.name}: not enabled (replay only)")

    subscribe = get_bars


class AlpacaLive(_Disabled):
    name = "alpaca-live"


class XStocks(_Disabled):
    name = "xstocks"


class Pyth(_Disabled):
    name = "pyth"
