"""Market-data providers. All are read-only and are asked ONCE per run (no polling).

  offline   built-in illustrative anchors, no network, no key. Always available.
  yfinance  unofficial Yahoo Finance wrapper; may rate-limit, change or break at any time.
  alpaca    Alpaca Market Data API (latest trade). Needs ALPACA_KEY_ID / ALPACA_SECRET_KEY. Never places orders.

A provider that fails raises ProviderError; fetch_with_fallback then uses `offline` and records why.
Keys are read from the environment / .env and are never logged, written or put in an error message."""

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Illustrative round-ish prices in cents. NOT quotes; they only make offline runs look plausible.
OFFLINE_ANCHORS = {
    "AAPL": 19000, "MSFT": 42000, "GOOGL": 17500, "AMZN": 18500,
    "NVDA": 12000, "TSLA": 25000, "META": 52000, "JPM": 20000,
}
DEFAULT_SYMBOLS = tuple(OFFLINE_ANCHORS)


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class Quotes:
    provider: str
    anchors: dict          # symbol -> integer cents
    interval: str
    timezone: str
    note: str = ""


def to_cents(value) -> int:
    """Provider prices arrive as floats/strings; this is the only place they become integer cents."""
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise ProviderError(f"unreadable price {value!r}") from None
    if not d.is_finite() or d <= 0:
        raise ProviderError(f"non-positive or non-finite price {value!r}")
    cents = int((d * 100).to_integral_value())
    if cents < 1:
        raise ProviderError(f"price {value!r} rounds to zero cents")
    return cents


def check_symbols(symbols) -> list:
    out = []
    for s in symbols:
        if not isinstance(s, str) or not SYMBOL_RE.match(s):
            raise ProviderError(f"bad symbol {s!r}")
        if s not in out:
            out.append(s)
    if not out:
        raise ProviderError("no symbols")
    return out


class OfflineProvider:
    name = "offline"

    def fetch(self, symbols, interval) -> Quotes:
        symbols = check_symbols(symbols)
        missing = [s for s in symbols if s not in OFFLINE_ANCHORS]
        if missing:
            raise ProviderError(f"no built-in anchor for {', '.join(missing)} "
                                f"(offline knows {', '.join(OFFLINE_ANCHORS)})")
        return Quotes("offline", {s: OFFLINE_ANCHORS[s] for s in symbols}, interval, "UTC",
                      "built-in illustrative anchors, not market data")


class YFinanceProvider:
    """Unofficial. `download` is injectable for tests: (symbols, interval) -> ({symbol: last close}, timezone)."""
    name = "yfinance"

    def __init__(self, download=None, timeout: float = 10.0):
        self._download, self.timeout = download or self._yf_download, timeout

    def _yf_download(self, symbols, interval):
        try:
            import yfinance as yf
        except ImportError:
            raise ProviderError("yfinance is not installed (pip install -e '.[sim]')") from None
        period = "5d"  # a few days of bars so a weekend/holiday still has a last close
        df = yf.download(list(symbols), period=period, interval=interval, progress=False, threads=False,
                         auto_adjust=False, timeout=self.timeout)
        if df is None or len(df) == 0:
            raise ProviderError("yfinance returned no rows (rate limit, offline, or unknown symbols)")
        close = df["Close"]
        if getattr(close, "ndim", 2) == 1:  # single symbol on some versions
            close = close.to_frame(symbols[0])
        last = {}
        for s in symbols:
            if s in close.columns:
                col = close[s].dropna()
                if len(col):
                    last[s] = float(col.iloc[-1])
        tz = getattr(df.index, "tz", None)
        return last, str(tz) if tz is not None else "America/New_York"

    def fetch(self, symbols, interval) -> Quotes:
        symbols = check_symbols(symbols)
        try:
            last, tz = self._download(symbols, interval)
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"yfinance failed: {type(e).__name__}") from None
        missing = [s for s in symbols if s not in last]
        if missing:
            raise ProviderError(f"yfinance had no price for {', '.join(missing)}")
        return Quotes("yfinance", {s: to_cents(last[s]) for s in symbols}, interval, tz,
                      "last close from an unofficial source; may be delayed or wrong")


class AlpacaProvider:
    """Alpaca Market Data: latest trade per symbol (IEX feed). Read-only; there is no order code in this project."""
    name = "alpaca"
    URL = "https://data.alpaca.markets/v2/stocks/trades/latest"

    def __init__(self, key_id: str, secret: str, http=None, timeout: float = 10.0):
        self._key_id, self._secret, self._http, self.timeout = key_id, secret, http, timeout

    def fetch(self, symbols, interval) -> Quotes:
        symbols = check_symbols(symbols)
        try:
            import httpx
        except ImportError:
            raise ProviderError("httpx is not installed (pip install -e '.[sim]')") from None
        client = self._http or httpx.Client(timeout=self.timeout)
        try:
            r = client.get(self.URL, params={"symbols": ",".join(symbols), "feed": "iex"},
                           headers={"APCA-API-KEY-ID": self._key_id, "APCA-API-SECRET-KEY": self._secret})
        except Exception as e:  # never include the exception text: it can echo request details
            raise ProviderError(f"alpaca request failed: {type(e).__name__}") from None
        if r.status_code != 200:
            raise ProviderError(f"alpaca answered HTTP {r.status_code}")
        try:
            trades = r.json()["trades"]
            return Quotes("alpaca", {s: to_cents(trades[s]["p"]) for s in symbols}, interval, "UTC",
                          "latest IEX trade; free-plan data is delayed or partial")
        except (KeyError, TypeError, ValueError):
            raise ProviderError("alpaca response had no price for every symbol") from None


def read_dotenv(path) -> dict:
    """Minimal KEY=VALUE reader for the two Alpaca keys only; nothing else is read, nothing is exported."""
    wanted = {"ALPACA_KEY_ID", "ALPACA_SECRET_KEY"}
    out = {}
    try:
        for line in Path(path).read_text().splitlines():
            k, sep, v = line.partition("=")
            k = k.strip()
            if sep and k in wanted:
                out[k] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def alpaca_keys(env=None, dotenv_path=None):
    env = os.environ if env is None else env
    file = read_dotenv(dotenv_path) if dotenv_path else {}
    k = env.get("ALPACA_KEY_ID") or file.get("ALPACA_KEY_ID")
    s = env.get("ALPACA_SECRET_KEY") or file.get("ALPACA_SECRET_KEY")
    return (k, s) if k and s else None


@dataclass
class Fetched:
    quotes: Quotes
    requested: str
    fallback_reason: str | None = None
    attempts: list = field(default_factory=list)   # [{"provider", "ok", "error"}]


def fetch_with_fallback(requested, symbols, interval, *, env=None, dotenv_path=None, providers=None) -> Fetched:
    """requested: auto | yfinance | alpaca | offline. `providers` (name -> object) lets tests inject fakes.
    auto = alpaca if keys exist, then yfinance, then offline. Any failure ends at offline with the reason recorded."""
    if providers is None:
        providers = {"offline": OfflineProvider(), "yfinance": YFinanceProvider()}
        keys = alpaca_keys(env, dotenv_path)
        if keys:
            providers["alpaca"] = AlpacaProvider(*keys)
    order = {"auto": ["alpaca", "yfinance"], "yfinance": ["yfinance"], "alpaca": ["alpaca"], "offline": []}[requested]
    attempts = []
    for name in order:
        p = providers.get(name)
        if p is None:
            attempts.append({"provider": name, "ok": False, "error": "no API key configured" if name == "alpaca" else "unavailable"})
            continue
        try:
            q = p.fetch(symbols, interval)
            attempts.append({"provider": name, "ok": True, "error": None})
            return Fetched(q, requested, None, attempts)
        except ProviderError as e:
            attempts.append({"provider": name, "ok": False, "error": str(e)})
    q = providers.get("offline", OfflineProvider()).fetch(symbols, interval)  # may raise ProviderError: caller reports it
    attempts.append({"provider": "offline", "ok": True, "error": None})
    reason = None
    if requested != "offline":
        reason = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts if not a["ok"])
        if requested == "auto" and not reason:
            reason = "no provider available"
    return Fetched(q, requested, reason, attempts)
