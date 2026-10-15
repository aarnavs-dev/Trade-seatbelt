"""Generate fixtures/recon/: a gate log, a broker CSV that disagrees with it in known ways, and a trades CSV that
breaks the rules in known ways. Everything is hand-computed below; money is cents; fee = max(qty*1, 50).
The log is written by the real Gate (gate.engine.Gate) with a scripted clock, so it is a real hash-chained log.
T0 = 2026-10-09T14:30:00Z; "t+N" means N seconds after T0. Broker times alternate between Z and -04:00 (EDT).

PART 1  reconciliation (rules.yaml: AAPL ref 19000, MSFT ref 42000, band 5%, position limit 100)
Orders sent to the gate (L006 is sent twice; the resend is answered from memory and never logged):
 seq  id   order                    t+    result
  1   L001 BUY  AAPL 10 @19000        0   ACCEPT
  2   L002 BUY  AAPL 20 @19100       10   ACCEPT
  3   L003 SELL AAPL 15 @19200       20   ACCEPT
  4   L004 BUY  MSFT  5 @42000       30   ACCEPT
  5   L005 SELL MSFT  5 @42100       40   ACCEPT
  6   L006 BUY  AAPL 10 @21000       45   REJECT PRICE_BAND (|2000|*100 > 19000*5)       [resend: no line]
  7   L007 BUY  AAPL 10 @19050       50   ACCEPT   (broker has no such row)
  8   L008 SELL AAPL 10 @19300       60   ACCEPT   (broker says qty 8)
  9   L009 BUY  MSFT 10 @41900       70   ACCEPT   (broker says price 41950)
 10   L010 BUY  AAPL  3 @19000      100   ACCEPT   (broker row has no id, at t+105: exactly 5s = tolerance, inclusive)
 11   L011 BUY  MSFT  2 @42000      120   ACCEPT   \\ two identical fills 2s apart; the broker has ONE id-less row at
 12   L012 BUY  MSFT  2 @42000      122   ACCEPT   /  t+121 that fits both: AMBIGUOUS
 13   L013 SELL AAPL  1 @19000      140   ACCEPT   (broker id-less row at t+146: 6s > 5s -> not a match)
 13 events, 12 ACCEPTED fills, 1 REJECT. (Positions stay under 100; realized P&L stays positive until seq 13
 closes 1 share at -100, a loss streak of 1, so every ACCEPT above is what the rules say.)

Broker CSV rows (row 1 is the header):
 row  id    what                           expected finding
  2   L001  10 @19000 t+1  (Z)             MATCHED seq 1
  3   L002  20 @19100 t+11 (-04:00)        MATCHED seq 2
  4   L002  exact copy of row 3            MISSING_IN_LOG (duplicate; related seq 2)
  5   L003  15 @19200                      MATCHED seq 3
  6   L004   5 @42000                      MATCHED seq 4
  7   L005   5 @42100                      MATCHED seq 5
  8   L005  exact copy of row 7            MISSING_IN_LOG (duplicate; related seq 5)
  9   L008  qty 8 @19300                   QTY_MISMATCH seq 8
 10   L009  10 @41950                      PRICE_MISMATCH seq 9
 11   -     AAPL BUY 3 @19000 at t+105     MATCHED seq 10 (|105-100| = 5s <= 5s)
 12   -     MSFT BUY 2 @42000 at t+121     AMBIGUOUS seqs 11,12
 13   X900  AAPL BUY 1 @19000              MISSING_IN_LOG (id unknown to the log)
 14   -     AAPL SELL 1 @19000 at t+146    MISSING_IN_LOG (and seq 13 MISSING_IN_BROKER)
 15   L006  AAPL BUY 10 @21000             MISSING_IN_LOG (broker filled what the gate REJECTED at seq 6)
 and seq 7 -> MISSING_IN_BROKER, seq 13 -> MISSING_IN_BROKER.
Accounting: 12 log fills = 6 MATCHED + 1 QTY + 1 PRICE + 2 AMBIGUOUS + 2 MISSING_IN_BROKER.
            14 rows     = 6 MATCHED + 1 QTY + 1 PRICE + 1 AMBIGUOUS + 5 MISSING_IN_LOG.
Counts: MATCHED 6, MISSING_IN_BROKER 2, MISSING_IN_LOG 5, QTY_MISMATCH 1, PRICE_MISMATCH 1, AMBIGUOUS 1.

Implied difference = broker minus log; BUY +qty and pays qty*price, SELL -qty and receives it; fees and AMBIGUOUS excluded.
 MISSING_IN_LOG  row 4  BUY  AAPL 20 @19100   AAPL +20   cash -20*19100 = -382000
                 row 8  SELL MSFT  5 @42100   MSFT  -5   cash +5*42100  = +210500
                 row 13 BUY  AAPL  1 @19000   AAPL  +1   cash           =  -19000
                 row 14 SELL AAPL  1 @19000   AAPL  -1   cash           =  +19000
                 row 15 BUY  AAPL 10 @21000   AAPL +10   cash           = -210000
 MISSING_IN_BROKER (remove the log fill)
                 seq 7  BUY  AAPL 10 @19050   AAPL -10   cash +10*19050 = +190500
                 seq 13 SELL AAPL  1 @19000   AAPL  +1   cash -19000
 QTY_MISMATCH    seq 8 vs row 9: SELL 8 vs SELL 10 @19300   AAPL +2   cash (8-10)*19300 = -38600
 PRICE_MISMATCH  seq 9 vs row 10: BUY 10 @41950 vs @41900  MSFT  0   cash -10*(41950-41900) = -500
 AAPL position: +20 +1 -1 +10 -10 +1 +2 = +23.   MSFT: -5.
 Cash: -382000 +210500 -19000 +19000 -210000 +190500 -19000 -38600 -500 = -249100  (-$2,491.00)

PART 2  counterfactual (rules.yaml: position 100, band 5% of AAPL 19000, daily loss 5000, 3 losses -> 300s cooldown)
Trades in FILE order (row = file line, header is row 1). t+ is seconds after T0. Rows 2 and 3 share t+0 (tie -> file
order), row 18 is out of order in the file (it happened at t+40) to exercise the sort.
 row id  trade                         t+
  2  C01 BUY  AAPL  60 @19000            0
  3  C02 BUY  AAPL  50 @19100            0
  4  C03 SELL AAPL  30 @19300           20
  5  C04 BUY  AAPL  10 @21000           30
  6  C05 BUY  MSFT  10 @42000          100
  7  C06 SELL MSFT  10 @41900          110
  8  C07 BUY  MSFT  10 @42000          120
  9  C08 SELL MSFT  10 @41900          130
 10  C09 BUY  MSFT  10 @42000          140
 11  C10 SELL MSFT  10 @41900          150
 12  C11 BUY  MSFT  10 @42000          160
 13  -   SELL MSFT  10 @42100          170   (no id -> synthetic csv-row-13)
 14  C13 BUY  MSFT 100 @42000          500
 15  C14 SELL MSFT 100 @41000          510
 16  C15 BUY  MSFT  10 @41000          520
 17  C16 SELL MSFT  10 @41500          530
 18  C17 SELL AAPL  80 @18900           40
Original (no rules, FIFO, 17 trades):
 AAPL lots 60@19000, 50@19100. Row4 closes 30@19000: +30*300 = +9000. Row5 adds 10@21000. Row18 closes 30@19000
 (-100*30 = -3000) and 50@19100 (-200*50 = -10000); 10@21000 stays open. AAPL gross = 9000-3000-10000 = -4000.
 AAPL fees: 60+50+50+50+80 = 290.   (row2 qty 60 -> 60; rows 3,4,5 -> 50 minimum; row18 qty 80 -> 80)
 MSFT: three losing round trips rows 6-11: 3*(-10*100) = -3000; rows 12,13 +10*100 = +1000;
 rows 14,15 100*(41000-42000) = -100000; rows 16,17 10*(41500-41000) = +5000.  Gross = -3000+1000-100000+5000 = -97000.
 MSFT fees: 17 - 5 AAPL = 12 trades; rows 6-13 are 8 trades at 50 = 400; rows 14-17: 100+100+50+50 = 300; total 700.
 Total gross -101000, fees 990, net -101990. Open: AAPL +10.
With the rules (sequential, each decision sees only accepted trades):
 t0   row2  BUY 60: position 60 <= 100                              ACCEPT  fee 60
 t0   row3  BUY 50: position would be 110 > 100                     BLOCK MAX_POSITION (limit 100, value 110)
 t20  row4  SELL 30 @19300: closes 30@19000 +9000, fee 50           ACCEPT  net so far 9000-110 = 8890
 t30  row5  BUY 10 @21000: |2000|*100 > 19000*5                     BLOCK PRICE_BAND
 t40  row18 SELL 80 @18900: position 30 -> -50 (|50| <= 100); band ok; realized net 8890 > -5000; no cooldown
                                                                    ACCEPT  closes 30@19000 = -3000, opens short 50@18900,
                                                                    fee 80. Net 8890-3000-80 = 5810. The loss (-3080) is streak 1.
 t100 row6  BUY MSFT 10                                             ACCEPT  fee 50
 t110 row7  SELL 10 @41900: -1000, fee 50                           ACCEPT  loss -1050 -> streak 2
 t120 row8  BUY 10                                                  ACCEPT  fee 50
 t130 row9  SELL 10 @41900: -1000, fee 50                           ACCEPT  loss -1050 -> streak 3 -> cooldown until t+430
 t140 row10 BUY 10 (increases risk, 140 < 430)                      BLOCK LOSS_STREAK_COOLDOWN
 t150 row11 SELL 10 (flat -> short 10, increases risk)              BLOCK LOSS_STREAK_COOLDOWN
 t160 row12 BUY 10                                                  BLOCK LOSS_STREAK_COOLDOWN
 t170 row13 SELL 10 (short)                                         BLOCK LOSS_STREAK_COOLDOWN
 t500 row14 BUY MSFT 100: after cooldown, net 3610 > -5000, 100<=100 ACCEPT  fee 100
 t510 row15 SELL 100 @41000: closes 100@42000 = -100000, fee 100    ACCEPT  (reducing risk is always allowed)
 t520 row16 BUY 10: realized net -96590 <= -5000, increases risk    BLOCK MAX_DAILY_LOSS (limit 5000, value 96590)
 t530 row17 SELL 10 (flat -> short, increases risk)                 BLOCK MAX_DAILY_LOSS
 Net after row9 = 5810 -50 (row6) -1050 -50 (row8) -1050 = 3610.  After row14: 3610-100 = 3510.  After row15:
 3510 - 100000 - 100 = -96590.
 Accepted rows: 2,4,18,6,7,8,9,14,15 (9). Blocked rows: 3,5,10,11,12,13,16,17 (8):
 MAX_POSITION 1, PRICE_BAND 1, LOSS_STREAK_COOLDOWN 4, MAX_DAILY_LOSS 2.
 Gross: +9000 -3000 -1000 -1000 -100000 = -96000.  Fees: 60+50+80+50+50+50+50+100+100 = 590.  Net -96590.
 Open at the end: AAPL short 50.  Impact (with rules minus original) = -96590 - (-101990) = +5400:
 gross -96000 - (-101000) = +5000; fees 590 - 990 = -400; net = gross - fees, so +5000 + 400 = +5400.
 Row 18 is why this is not a subtraction: the original SELL 80 closed a long, with the rules it flips to a short.
"""

import asyncio
import calendar
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import orjson

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rules import load_rules  # noqa: E402
from gate.engine import Gate  # noqa: E402  (used as a client of the real gate; nothing in gate/ is changed)

T0 = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
T0_NS = calendar.timegm(T0.timetuple()) * 10**9
EDT = timezone(timedelta(hours=-4))
HEADER = "ts,client_order_id,symbol,side,qty,price,fee"

# (id, side, symbol, qty, price, t+seconds); the resend of L006 is handled in build_log
LOG_ORDERS = [
    ("L001", "BUY", "AAPL", 10, 19000, 0), ("L002", "BUY", "AAPL", 20, 19100, 10),
    ("L003", "SELL", "AAPL", 15, 19200, 20), ("L004", "BUY", "MSFT", 5, 42000, 30),
    ("L005", "SELL", "MSFT", 5, 42100, 40), ("L006", "BUY", "AAPL", 10, 21000, 45),
    ("L007", "BUY", "AAPL", 10, 19050, 50), ("L008", "SELL", "AAPL", 10, 19300, 60),
    ("L009", "BUY", "MSFT", 10, 41900, 70), ("L010", "BUY", "AAPL", 3, 19000, 100),
    ("L011", "BUY", "MSFT", 2, 42000, 120), ("L012", "BUY", "MSFT", 2, 42000, 122),
    ("L013", "SELL", "AAPL", 1, 19000, 140),
]


def stamp(t: int, tz=timezone.utc) -> str:
    d = (T0 + timedelta(seconds=t)).astimezone(tz)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if tz is timezone.utc else d.isoformat()


def broker_csv() -> str:
    Z, E = timezone.utc, EDT
    rows = [  # (t+, tz, id, symbol, side, qty, price, fee)
        (1, Z, "L001", "AAPL", "BUY", 10, 19000, 50), (11, E, "L002", "AAPL", "BUY", 20, 19100, 50),
        (11, E, "L002", "AAPL", "BUY", 20, 19100, 50), (21, Z, "L003", "AAPL", "SELL", 15, 19200, 50),
        (31, E, "L004", "MSFT", "BUY", 5, 42000, 50), (41, Z, "L005", "MSFT", "SELL", 5, 42100, 50),
        (41, Z, "L005", "MSFT", "SELL", 5, 42100, 50), (61, E, "L008", "AAPL", "SELL", 8, 19300, 50),
        (71, Z, "L009", "MSFT", "BUY", 10, 41950, 50), (105, E, "", "AAPL", "BUY", 3, 19000, ""),
        (121, Z, "", "MSFT", "BUY", 2, 42000, ""), (130, Z, "X900", "AAPL", "BUY", 1, 19000, 50),
        (146, E, "", "AAPL", "SELL", 1, 19000, ""), (46, Z, "L006", "AAPL", "BUY", 10, 21000, 50),
    ]
    return "\n".join([HEADER] + [f"{stamp(t, tz)},{i},{s},{sd},{q},{p},{f}" for t, tz, i, s, sd, q, p, f in rows]) + "\n"


def cf_csv() -> str:
    rows = [  # file order == row number - 1 ... see the docstring
        (0, "C01", "BUY", "AAPL", 60, 19000), (0, "C02", "BUY", "AAPL", 50, 19100),
        (20, "C03", "SELL", "AAPL", 30, 19300), (30, "C04", "BUY", "AAPL", 10, 21000),
        (100, "C05", "BUY", "MSFT", 10, 42000), (110, "C06", "SELL", "MSFT", 10, 41900),
        (120, "C07", "BUY", "MSFT", 10, 42000), (130, "C08", "SELL", "MSFT", 10, 41900),
        (140, "C09", "BUY", "MSFT", 10, 42000), (150, "C10", "SELL", "MSFT", 10, 41900),
        (160, "C11", "BUY", "MSFT", 10, 42000), (170, "", "SELL", "MSFT", 10, 42100),
        (500, "C13", "BUY", "MSFT", 100, 42000), (510, "C14", "SELL", "MSFT", 100, 41000),
        (520, "C15", "BUY", "MSFT", 10, 41000), (530, "C16", "SELL", "MSFT", 10, 41500),
        (40, "C17", "SELL", "AAPL", 80, 18900),
    ]
    return "\n".join([HEADER] + [f"{stamp(t)},{i},{s},{sd},{q},{p},"
                                 for t, i, sd, s, q, p in rows]) + "\n"


async def build_log(path: str, rules_path: str) -> None:
    clock_values = iter(T0_NS + t * 10**9 for *_, t in LOG_ORDERS)  # one tick per non-resend order
    # record_rules=False: this committed fixture is deliberately a v1 log (no rules record), so its seqs match the hand-computed answers
    gate = Gate(load_rules(rules_path), path, "none", clock=lambda: next(clock_values), record_rules=False)
    await gate.start()
    for oid, side, sym, qty, price, _t in LOG_ORDERS:
        order = orjson.dumps(dict(client_order_id=oid, side=side, symbol=sym, qty=qty, price=price)) + b"\n"
        await (await gate.submit(order))
        if oid == "L006":  # the resend: answered from memory, no log line, no clock tick
            await (await gate.submit(order))
    await gate.stop()


EXPECTED_RECON = {
    "log_fills": 12, "csv_rows": 14,
    "counts": {"AMBIGUOUS": 1, "MATCHED": 6, "MISSING_IN_BROKER": 2, "MISSING_IN_LOG": 5,
               "PRICE_MISMATCH": 1, "QTY_MISMATCH": 1},
    "findings": [  # [category, log seqs, csv rows]
        ["MATCHED", [1], [2]], ["MATCHED", [2], [3]], ["MATCHED", [3], [5]], ["MATCHED", [4], [6]],
        ["MATCHED", [5], [7]], ["MATCHED", [10], [11]],
        ["QTY_MISMATCH", [8], [9]], ["PRICE_MISMATCH", [9], [10]], ["AMBIGUOUS", [11, 12], [12]],
        ["MISSING_IN_BROKER", [7], []], ["MISSING_IN_BROKER", [13], []],
        ["MISSING_IN_LOG", [], [4]], ["MISSING_IN_LOG", [], [8]], ["MISSING_IN_LOG", [], [13]],
        ["MISSING_IN_LOG", [], [14]], ["MISSING_IN_LOG", [], [15]],
    ],
    "implied_position": {"AAPL": 23, "MSFT": -5}, "implied_cash_excl_fees": -249100,
    "excluded_ambiguous": {"findings": 1, "log_fills": 2, "csv_rows": 1},
}
EXPECTED_CF = {
    "orders": 17, "accepted": 9, "blocked": 8,
    "accepted_rows": [2, 4, 6, 7, 8, 9, 14, 15, 18],
    "blocked_by_rule": {"LOSS_STREAK_COOLDOWN": 4, "MAX_DAILY_LOSS": 2, "MAX_POSITION": 1, "PRICE_BAND": 1},
    "evidence": {  # row: [rule, limit, value]
        "3": ["MAX_POSITION", 100, 110],
        "5": ["PRICE_BAND", "5% of reference 19000", "21000 (deviation 2000)"],
        "10": ["LOSS_STREAK_COOLDOWN", "cooldown until 2026-10-09T14:37:10Z", "order at 2026-10-09T14:32:20Z"],
        "11": ["LOSS_STREAK_COOLDOWN", "cooldown until 2026-10-09T14:37:10Z", "order at 2026-10-09T14:32:30Z"],
        "12": ["LOSS_STREAK_COOLDOWN", "cooldown until 2026-10-09T14:37:10Z", "order at 2026-10-09T14:32:40Z"],
        "13": ["LOSS_STREAK_COOLDOWN", "cooldown until 2026-10-09T14:37:10Z", "order at 2026-10-09T14:32:50Z"],
        "16": ["MAX_DAILY_LOSS", 5000, 96590],
        "17": ["MAX_DAILY_LOSS", 5000, 96590],
    },
    "original": {"gross": -101000, "fees": 990, "net": -101990, "positions": {"AAPL": 10}},
    "with_rules": {"gross": -96000, "fees": 590, "net": -96590, "positions": {"AAPL": -50}},
    "impact": {"gross": 5000, "fees": -400, "net": 5400},
}


def main(out: str, rules: str) -> None:
    os.makedirs(out, exist_ok=True)
    log = os.path.join(out, "gate_log.jsonl")  # not *.log: those are gitignored, this fixture is committed
    if os.path.exists(log):
        os.remove(log)
    asyncio.run(build_log(log, rules))
    for name, body in (("broker_fills.csv", broker_csv()), ("cf_trades.csv", cf_csv())):
        with open(os.path.join(out, name), "w") as f:
            f.write(body)
    for name, exp in (("expected_recon.json", EXPECTED_RECON), ("expected_cf.json", EXPECTED_CF)):
        with open(os.path.join(out, name), "w") as f:
            json.dump(exp, f, indent=2, sort_keys=True)
            f.write("\n")
    print(f"wrote {out}/gate_log.jsonl, broker_fills.csv, cf_trades.csv and the expected_*.json files")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="fixtures/recon")
    ap.add_argument("--rules", default="rules.yaml")
    a = ap.parse_args()
    main(a.out, a.rules)
