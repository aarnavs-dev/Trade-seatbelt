"""Parse a broker-style fills CSV. Shared by reconciliation and the counterfactual replay.

Columns (header row required, any order, names exact and case-sensitive):
  ts                 required. ISO-8601 with an explicit offset (2026-10-09T14:30:00Z, ...T10:30:00.25-04:00,
                     up to 9 fractional digits) or an integer of epoch NANOSECONDS (18-19 digits, the gate's unit).
                     Naive times and integers of any other size (seconds, ms, us) are rejected, not guessed.
  client_order_id    optional (empty cell = absent). Same pattern the gate enforces: [A-Za-z0-9_-]{1,64}.
  symbol, side       required. side is exactly BUY or SELL.
  qty                required whole number of shares, 1..10**6.
  price              required integer minor units (cents), 1..10**9. "190.00" is a float-as-money: rejected.
  fee                optional integer minor units, 0..10**9.

Row numbers: row 1 is the header, the first data row is row 2 (what a spreadsheet shows). Every error names its row.
Strict mode (default) raises CsvError at the first bad row. Lenient mode collects every bad row and parses the rest.
A bad header is always fatal because no row can be interpreted without it.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

REQUIRED = ("ts", "symbol", "side", "qty", "price")
OPTIONAL = ("client_order_id", "fee")
MAX_QTY, MAX_MONEY = 10**6, 10**9  # keeps every product and sum far inside 64-bit ints (reports are JSON)

_ISO = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SYMBOL = re.compile(r"^[A-Za-z0-9._-]{1,16}$")
_DIGITS = re.compile(r"^[0-9]+$")
_DECIMAL = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+[eE][+-]?\d+|\d*\.\d+[eE][+-]?\d+)$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CsvError(Exception):
    """row is None for file-level problems (encoding), 1 for the header, 2.. for data rows."""

    def __init__(self, row: int | None, message: str):
        super().__init__(f"row {row}: {message}" if row is not None else f"file: {message}")
        self.row, self.message = row, message


class RowError(NamedTuple):
    row: int
    message: str


@dataclass(frozen=True)
class Fill:
    row: int
    ts: int  # epoch ns, UTC
    client_order_id: str | None
    symbol: str
    side: str
    qty: int
    price: int  # minor units
    fee: int | None

    def content(self) -> tuple:
        """Everything except the row number: what makes two rows interchangeable."""
        return (self.ts, self.symbol, self.side, self.qty, self.price, self.client_order_id or "",
                -1 if self.fee is None else self.fee)


@dataclass
class ParseResult:
    fills: list[Fill]
    errors: list[RowError]
    rows_total: int  # data rows seen, good or bad


def fmt_ts(ns: int) -> str:
    """UTC ISO-8601 for display; fractional digits only when needed."""
    secs, frac = divmod(ns, 10**9)
    base = (_EPOCH + timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{frac:09d}Z" if frac else f"{base}Z"


def _parse_ts(s: str) -> int:
    if s.isascii() and s.isdigit():
        if not 10**18 <= int(s) < 10**19:
            raise ValueError(f"integer ts '{s}' has {len(s)} digits; epoch nanoseconds have 19 "
                             "(this looks like seconds, ms or us, which are not guessed)")
        return int(s)
    m = _ISO.match(s)
    if not m:
        raise ValueError(f"ts '{s}' must be ISO-8601 with an explicit offset (e.g. 2026-10-09T14:30:00Z or "
                         "2026-10-09T10:30:00-04:00) or integer epoch nanoseconds; times without an offset are rejected")
    base, frac, tz = m.groups()
    try:
        dt = datetime.fromisoformat(base + ("+00:00" if tz == "Z" else tz))
    except ValueError:
        raise ValueError(f"ts '{s}' is not a valid date/time") from None
    delta = dt - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 10**9 + int((frac or "").ljust(9, "0"))


def _int_field(name: str, s: str, lo: int, hi: int) -> int:
    if not _DIGITS.match(s):
        if _DECIMAL.match(s):
            raise ValueError(f"{name} '{s}' is a decimal; {name} must be a whole number"
                             + (" of minor units (floats as money are rejected)" if name != "qty" else ""))
        raise ValueError(f"{name} '{s}' is not a non-negative integer")
    v = int(s)
    if not lo <= v <= hi:
        raise ValueError(f"{name} {v} is outside {lo}..{hi}")
    return v


def _parse_row(row: int, header: list[str], rec: list[str]) -> Fill:
    if len(rec) != len(header):
        raise ValueError(f"expected {len(header)} fields, found {len(rec)}")
    cell = dict(zip(header, rec))
    problems = []

    def get(name, fn):
        v = cell.get(name, "")
        if v != v.strip():
            problems.append(f"{name} '{v}' has leading or trailing whitespace")
            return None
        try:
            return fn(v)
        except ValueError as e:
            problems.append(str(e))
            return None

    def side(v):
        if v not in ("BUY", "SELL"):
            raise ValueError(f"side '{v}' must be exactly BUY or SELL")
        return v

    def symbol(v):
        if not _SYMBOL.match(v):
            raise ValueError(f"symbol '{v}' must match [A-Za-z0-9._-]{{1,16}}")
        return v

    def oid(v):
        if v and not _ID.match(v):
            raise ValueError(f"client_order_id '{v}' must match [A-Za-z0-9_-]{{1,64}}")
        return v or None

    ts = get("ts", _parse_ts)
    sym, sd, oi = get("symbol", symbol), get("side", side), get("client_order_id", oid)
    qty = get("qty", lambda v: _int_field("qty", v, 1, MAX_QTY))
    price = get("price", lambda v: _int_field("price", v, 1, MAX_MONEY))
    fee = get("fee", lambda v: _int_field("fee", v, 0, MAX_MONEY) if v else None)
    if problems:
        raise ValueError("; ".join(problems))
    return Fill(row, ts, oi, sym, sd, qty, price, fee)


def _check_header(header: list[str]) -> None:
    if len(set(header)) != len(header):
        raise CsvError(1, f"duplicate column name in header {header}")
    unknown = [h for h in header if h not in REQUIRED + OPTIONAL]
    if unknown:
        raise CsvError(1, f"unknown column(s) {unknown}; allowed: {list(REQUIRED + OPTIONAL)}")
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise CsvError(1, f"missing required column(s) {missing}")


def parse_fills_csv(path, lenient: bool = False) -> ParseResult:
    try:
        text = open(path, "rb").read().decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise CsvError(None, f"not valid UTF-8 ({e.reason} at byte {e.start})") from None
    it = csv.reader(io.StringIO(text, newline=""), strict=True)
    n = 0  # records consumed so far; the header is record 1
    try:
        header = next(it)
        n = 1
    except StopIteration:
        raise CsvError(1, "file is empty, expected a header row") from None
    except csv.Error as e:
        raise CsvError(1, f"malformed CSV: {e}") from None
    _check_header(header)
    res = ParseResult([], [], 0)
    while True:
        try:
            rec = next(it)
        except StopIteration:
            break
        except csv.Error as e:
            n += 1
            err = RowError(n, f"malformed CSV: {e}")
        else:
            n += 1
            try:
                if not rec:
                    raise ValueError("blank line")
                res.fills.append(_parse_row(n, header, rec))
                res.rows_total += 1
                continue
            except ValueError as e:
                err = RowError(n, str(e))
        res.rows_total += 1
        if not lenient:
            raise CsvError(err.row, err.message)
        res.errors.append(err)
    return res
