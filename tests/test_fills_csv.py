"""Ingestion of the broker CSV: money is integer minor units, and every bad row is reported by row number."""

import pytest

from auditor.fills_csv import CsvError, fmt_ts, parse_fills_csv

HEAD = "ts,client_order_id,symbol,side,qty,price,fee\n"
GOOD = "2026-10-09T14:30:00Z,A1,AAPL,BUY,10,19000,50\n"


def write(tmp_path, text, name="f.csv"):
    p = tmp_path / name
    p.write_bytes(text.encode() if isinstance(text, str) else text)
    return p


def test_parses_a_good_file_into_integer_minor_units(tmp_path):
    r = parse_fills_csv(write(tmp_path, HEAD + GOOD + "2026-10-09T14:30:01Z,,MSFT,SELL,5,42100,\n"))
    assert r.errors == [] and r.rows_total == 2
    a, b = r.fills
    assert (a.row, a.client_order_id, a.symbol, a.side, a.qty, a.price, a.fee) == (2, "A1", "AAPL", "BUY", 10, 19000, 50)
    assert (b.row, b.client_order_id, b.fee) == (3, None, None)  # empty cells are "absent", not 0
    assert all(isinstance(x, int) for x in (a.ts, a.qty, a.price, a.fee))


def test_timezone_offsets_are_converted_exactly(tmp_path):
    # days 1970-01-01..2026-10-09 = 56*365 + 14 leap days (1972..2024) + 273 (Jan-Sep) + 8 = 20,735;
    # 2026-10-09T14:30:00Z = 20,735*86400 + 14.5*3600 = 1_791_504_000 + 52_200 = 1_791_556_200 s
    ns = 1_791_556_200 * 10**9
    rows = ["2026-10-09T14:30:00Z", "2026-10-09T10:30:00-04:00", "2026-10-09T20:00:00+05:30",
            "2026-10-09T14:30:00.250Z", "2026-10-09T14:30:00.123456789Z", str(ns)]
    r = parse_fills_csv(write(tmp_path, HEAD + "".join(f"{t},,AAPL,BUY,1,100,\n" for t in rows)))
    got = [f.ts for f in r.fills]
    assert got == [ns, ns, ns, ns + 250_000_000, ns + 123_456_789, ns]
    assert fmt_ts(ns + 250_000_000) == "2026-10-09T14:30:00.250000000Z" and fmt_ts(ns) == "2026-10-09T14:30:00Z"


def test_column_order_is_free_and_optional_columns_may_be_absent(tmp_path):
    r = parse_fills_csv(write(tmp_path, "price,qty,side,symbol,ts\n19000,10,BUY,AAPL,2026-10-09T14:30:00Z\n"))
    assert r.fills[0].client_order_id is None and r.fills[0].fee is None and r.fills[0].price == 19000


def test_utf8_bom_and_crlf_are_tolerated(tmp_path):
    r = parse_fills_csv(write(tmp_path, "﻿" + (HEAD + GOOD).replace("\n", "\r\n")))
    assert len(r.fills) == 1


@pytest.mark.parametrize("bad_row,needle", [
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10,190.00,50", "floats as money"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10,1.9e4,50", "decimal"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10,19000,0.5", "decimal"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10.5,19000,50", "whole number"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,0,19000,50", "qty 0 is outside"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,-3,19000,50", "not a non-negative integer"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10,0,50", "price 0 is outside"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10,-19000,50", "not a non-negative integer"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10,19000,-1", "not a non-negative integer"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10,9999999999,50", "outside"),
    ("2026-10-09T14:30:00Z,A1,AAPL,buy,10,19000,50", "exactly BUY or SELL"),
    ("2026-10-09T14:30:00Z,A1,AAPL,HOLD,10,19000,50", "exactly BUY or SELL"),
    ("2026-10-09T14:30:00,A1,AAPL,BUY,10,19000,50", "without an offset are rejected"),
    ("2026-10-09 14:30:00Z,A1,AAPL,BUY,10,19000,50", "ISO-8601"),
    ("2026-13-09T14:30:00Z,A1,AAPL,BUY,10,19000,50", "not a valid date"),
    ("1791556200,A1,AAPL,BUY,10,19000,50", "looks like seconds"),  # epoch seconds are never guessed
    ("1791556200000,A1,AAPL,BUY,10,19000,50", "looks like seconds, ms or us"),
    ("2026-10-09T14:30:00Z,bad id!,AAPL,BUY,10,19000,50", "client_order_id"),
    ("2026-10-09T14:30:00Z,A1,,BUY,10,19000,50", "symbol"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10,19000", "expected 7 fields, found 6"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY,10,19000,50,9", "expected 7 fields, found 8"),
    ("2026-10-09T14:30:00Z,A1,AAPL,BUY, 10,19000,50", "whitespace"),
    ("", "blank line"),
])
def test_bad_row_fails_the_file_and_names_its_row(tmp_path, bad_row, needle):
    # the bad row is the 4th data line => row 5 (row 1 is the header)
    text = HEAD + GOOD + GOOD.replace("A1", "A2") + GOOD.replace("A1", "A3") + bad_row + "\n" + GOOD.replace("A1", "A5")
    with pytest.raises(CsvError) as e:
        parse_fills_csv(write(tmp_path, text))
    assert e.value.row == 5 and needle in e.value.message and str(e.value).startswith("row 5: ")


def test_strict_mode_reports_the_first_bad_row_not_a_later_one(tmp_path):
    text = HEAD + GOOD + "x,A,AAPL,BUY,10,19000,\n" + GOOD + "2026-10-09T14:30:00Z,B,AAPL,BUY,1.5,100,\n"
    with pytest.raises(CsvError) as e:
        parse_fills_csv(write(tmp_path, text))
    assert e.value.row == 3


def test_lenient_collects_every_bad_row_and_keeps_the_good_ones(tmp_path):
    text = (HEAD + GOOD + "x,A,AAPL,BUY,10,19000,\n" + GOOD.replace("A1", "A3") + "\n"
            + "2026-10-09T14:30:00Z,B,AAPL,BUY,1.5,100,\n" + GOOD.replace("A1", "A6"))
    r = parse_fills_csv(write(tmp_path, text), lenient=True)
    assert [e.row for e in r.errors] == [3, 5, 6]
    assert [f.row for f in r.fills] == [2, 4, 7] and r.rows_total == 6


def test_one_row_can_report_several_problems(tmp_path):
    with pytest.raises(CsvError) as e:
        parse_fills_csv(write(tmp_path, HEAD + "2026-10-09T14:30:00Z,A,AAPL,BUY,1.5,190.00,\n"))
    assert "qty" in e.value.message and "price" in e.value.message


@pytest.mark.parametrize("text,needle", [
    ("", "empty"),
    ("ts,symbol,side,qty\n", "missing required column(s) ['price']"),
    ("ts,symbol,side,qty,price,venue\n", "unknown column"),
    ("ts,symbol,side,qty,price,qty\n", "duplicate column"),
])
def test_header_problems_are_fatal_even_when_lenient(tmp_path, text, needle):
    for lenient in (False, True):
        with pytest.raises(CsvError) as e:
            parse_fills_csv(write(tmp_path, text), lenient=lenient)
        assert e.value.row == 1 and needle in e.value.message


def test_unterminated_quote_names_the_row(tmp_path):
    with pytest.raises(CsvError) as e:
        parse_fills_csv(write(tmp_path, HEAD + GOOD + '2026-10-09T14:30:00Z,"A1,AAPL,BUY,10,19000,50\n'))
    assert e.value.row == 3 and "malformed" in e.value.message


def test_non_utf8_is_a_file_error(tmp_path):
    with pytest.raises(CsvError) as e:
        parse_fills_csv(write(tmp_path, HEAD.encode() + b"\xff\xfe,A,AAPL,BUY,1,1,\n"))
    assert e.value.row is None and str(e.value).startswith("file: ")
