"""Market simulator: a normal TCP client of the gate. It never writes or reads the log; the gate does.

Everything here is simulated. Prices come from a market-data snapshot (or built-in anchors) fed to a basic
random-walk model; execution is simulated by the gate (a full fill at the order price)."""

VERSION = 1
MAX_SPEED = 200.0          # orders per second, a pacing cap for the UI and CLI, not a benchmark
MIN_SPEED = 0.1
DEFAULT_SPEED = 5.0
DEFAULT_MAX_ORDERS = 100_000   # safety cap so a forgotten run cannot fill the disk
PROFILES = ("disciplined", "impulsive", "tilt")
PROVIDERS = ("auto", "yfinance", "alpaca", "offline")
INTERVALS = ("1d", "1h", "15m", "5m")
MAX_SYMBOLS = 12
