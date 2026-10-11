"""Historical-bar providers for the replay backend. Read-only: the only endpoint used is Alpaca's market-DATA bars
endpoint; there is no order code anywhere. Each provider returns (rows, timezone) where a row is
(symbol, bar_start_ms, o, h, l, c, v) with prices already integer cents. Keys are never logged or put in errors."""

from datetime import date, datetime, timedelta, timezone

from marketdata import INTERVAL_MS, MarketDataError, NoDataError
from sim.providers import ProviderError, alpaca_keys, to_cents

ALPACA_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"   # market data only
ALPACA_TF = {"1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min", "1h": "1Hour"}
# Yahoo's own intraday history limits (approximate, unofficial; they can change without notice).
YF_LOOKBACK_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "1h": 730}


def _cents(x):
    try:
        return to_cents(x)
    except ProviderError as e:
        raise MarketDataError(str(e)) from None


class AlpacaHistorical:
    name = "alpaca"

    def __init__(self, key_id, secret, http=None, timeout=15.0):
        self._k, self._s, self._http, self.timeout = key_id, secret, http, timeout

    def fetch(self, symbols, start: date, end: date, interval):
        """end is exclusive."""
        if interval not in ALPACA_TF:
            raise MarketDataError(f"alpaca: unsupported interval {interval}")
        try:
            import httpx
        except ImportError:
            raise MarketDataError("httpx is not installed (pip install -e '.[sim]')") from None
        client = self._http or httpx.Client(timeout=self.timeout)
        headers = {"APCA-API-KEY-ID": self._k, "APCA-API-SECRET-KEY": self._s}
        params = {"symbols": ",".join(symbols), "timeframe": ALPACA_TF[interval], "start": start.isoformat(),
                  "end": end.isoformat(), "feed": "iex", "limit": 10000, "adjustment": "raw"}
        rows, token = [], None
        for _ in range(200):  # page cap
            q = dict(params, **({"page_token": token} if token else {}))
            try:
                r = client.get(ALPACA_BARS_URL, params=q, headers=headers)
            except Exception as e:  # exception text can echo request details
                raise MarketDataError(f"alpaca request failed: {type(e).__name__}") from None
            if r.status_code != 200:
                raise MarketDataError(f"alpaca answered HTTP {r.status_code}")
            try:
                body = r.json()
                for sym, bars in (body.get("bars") or {}).items():
                    for b in bars:
                        t = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                        rows.append((sym, int(t.timestamp() * 1000), _cents(b["o"]), _cents(b["h"]), _cents(b["l"]),
                                     _cents(b["c"]), int(b.get("v", 0))))
                token = body.get("next_page_token")
            except (KeyError, TypeError, ValueError, AttributeError):
                raise MarketDataError("alpaca response was not in the expected shape") from None
            if not token:
                break
        return rows, "UTC"


class YFinanceHistorical:
    """Unofficial Yahoo wrapper. `download` is injectable: (symbols, start, end, interval) -> (rows, tz)."""
    name = "yfinance"

    def __init__(self, download=None, today=None):
        self._download, self._today = download or self._yf_download, today

    def fetch(self, symbols, start: date, end: date, interval):
        if interval not in YF_LOOKBACK_DAYS:
            raise MarketDataError(f"yfinance: unsupported interval {interval}")
        today = self._today or datetime.now(timezone.utc).date()
        limit = YF_LOOKBACK_DAYS[interval]
        if start < today - timedelta(days=limit):
            raise NoDataError(f"yfinance only serves {interval} bars for about the last {limit} days; "
                              f"{start} is older. Use Alpaca keys or a shorter/more recent range.")
        try:
            return self._download(symbols, start, end, interval)
        except MarketDataError:
            raise
        except Exception as e:
            raise MarketDataError(f"yfinance failed: {type(e).__name__}") from None

    @staticmethod
    def _yf_download(symbols, start, end, interval):
        try:
            import yfinance as yf
        except ImportError:
            raise MarketDataError("yfinance is not installed (pip install -e '.[sim]')") from None
        df = yf.download(list(symbols), start=start.isoformat(), end=end.isoformat(), interval=interval,
                         progress=False, threads=False, auto_adjust=False, group_by="column")
        if df is None or len(df) == 0:
            raise NoDataError("yfinance returned no rows (rate limit, offline, or no data for the range)")
        tz = getattr(df.index, "tz", None)
        rows = []
        for s in symbols:
            try:
                sub = df.xs(s, axis=1, level=1) if getattr(df.columns, "nlevels", 1) > 1 else df
            except KeyError:
                continue
            sub = sub.dropna(subset=["Open", "High", "Low", "Close"])
            stamps = sub.index.tz_localize("UTC") if tz is None else sub.index.tz_convert("UTC")
            for ts, o, h, l, c, v in zip(stamps, sub["Open"], sub["High"], sub["Low"], sub["Close"], sub["Volume"]):
                rows.append((s, int(ts.timestamp() * 1000), _cents(o), _cents(h), _cents(l), _cents(c), int(v or 0)))
        return rows, str(tz) if tz is not None else "UTC (assumed; Yahoo gave naive timestamps)"


def choose_provider(env=None, dotenv_path=None, **fakes):
    """Alpaca historical if keys are available, else yfinance. `fakes` lets tests inject providers."""
    if "provider" in fakes:
        return fakes["provider"]
    keys = alpaca_keys(env, dotenv_path)
    return AlpacaHistorical(*keys) if keys else YFinanceHistorical()
