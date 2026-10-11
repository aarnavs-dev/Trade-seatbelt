# Limitations

What this project does not do, does not guarantee, or has not shown. Measured figures come from
[PERFORMANCE.md](PERFORMANCE.md) and `bench/results/*.json`.

## Scope

- **Simulated only.** No real orders, money, broker or market data. An ACCEPT is an immediate full fill at the order's own
  price: no slippage, partial fills, queue position or rejects from a venue.
- **Reference prices are static** values in `rules.yaml`; the price band is measured against them, not a live market.
- **No** stock splits or other corporate actions, wash-sale or tax-lot rules, short-borrow costs, FX or multiple currencies.
  P&L is FIFO, in integer cents, with a per-share fee that has a per-order minimum.
- The brief's optional Gemini / ElevenLabs / Tiger Data integrations are **not built**.
- **Rate limit, daily loss and cooldown** are covered by unit and replay tests, not by the benchmark (it disables the first two
  and exercises the cooldown only in a short epilogue).

## Broker reconciliation and counterfactual

- **The broker CSV layout is our assumption.** Columns `ts, client_order_id (optional), symbol, side, qty, price, fee (optional)`,
  integer minor units, UTF-8. No real broker export has been parsed; a real one will need a mapping step this project does not have.
  Anything that does not fit is refused with its row number (row 1 = header) rather than coerced.
- **Matching tolerance.** Rows with a `client_order_id` match on the id alone; the timestamp is not compared at all, so an id match
  hours apart is still MATCHED. Rows without an id match on exactly (symbol, side, qty, price) with `|broker ts - log ts| <= 5 s`
  (inclusive, `--tolerance-seconds`). The 5 s default is a judgment call, not measured: too small makes real fills look missing,
  too large creates AMBIGUOUS cases. Without an id, a quantity or price difference cannot be told from two different trades, so it
  shows up as one MISSING_IN_BROKER plus one MISSING_IN_LOG, never as QTY_MISMATCH / PRICE_MISMATCH.
- **AMBIGUOUS is final.** If an id-less row has several candidate fills (or the reverse), or candidates chain together, nothing is paired,
  all members are cited, and they are excluded from the implied position/cash difference (their count is reported). The real
  position and cash difference is therefore only bounded by what the report says it left out.
- **Duplicates.** Several broker rows with one id that agree on symbol/side/qty/price are treated as one fill reported repeatedly: the
  earliest pairs with the log, each extra copy is MISSING_IN_LOG (it still counts in the implied difference, because a broker that
  booked it twice really holds it twice). If they disagree with each other the case is AMBIGUOUS.
- **What MISSING_IN_LOG can mean.** The broker did something the gate never saw, the gate rejected it (the finding says so), or the
  log's newest lines were deleted, which this project cannot detect (see tail truncation above). A MISSING_IN_BROKER can equally be
  an incomplete broker export. The report does not choose between these.
- **Only full fills.** No partial fills: one log fill is compared with one broker row. A broker splitting a fill into several rows
  shows as QTY_MISMATCH / AMBIGUOUS / MISSING, never as a sum. Fees are not reconciled and the implied cash difference is trade notional only.
- **Timezones.** A CSV `ts` must be ISO-8601 with an explicit offset (`Z`, `-04:00`) or integer epoch nanoseconds (19 digits). Times with no
  offset are refused because the zone would be a guess; integers of any other size are refused because the unit would be a guess. Offsets
  are converted to UTC nanoseconds exactly. Nothing checks that the offset is the right one: a broker that labels local time as `Z` looks
  like a systematic skew and will push id-less rows outside the tolerance. The gate log's own `ts` is the gate machine's wall clock.
- **Lenient mode** (`--lenient`) skips unreadable rows and says so at the top of the report; everything it then calls missing may only
  be a skipped row. Strict mode (default) fails the whole file at the first bad row. A bad header is fatal in both.
- **Reconciliation needs a verified log.** If the hash chain is broken the reconciliation is not run at all.
- **Counterfactual assumptions.** Every accepted order fills fully, immediately, at its CSV price; fees come from `rules.yaml` for both the
  original and the rules-applied side (the CSV fee column is ignored there); P&L is realized only, open positions are listed but not
  marked to market and can differ between the two runs; a repeated `client_order_id` is a resend and is ignored, as the gate would.
  Rows replay in `(ts, CSV row number)` order, so rows with an identical timestamp are decided by file order, and the live gate would
  have decided by arrival order. The replay uses the CSV timestamp as the time the gate would have stamped. The result describes what
  the rules would have decided on that sequence; it does not say the trader would have behaved the same after being blocked.
- **Counterfactual P&L is realized-only.** Open positions are not marked to market, so the original and rules-applied figures can leave
  different open positions (the fixture ends with AAPL +10 vs AAPL -50 short) that the P&L impact ignores.
- **The fixture dollar impact is illustrative.** The +$54.00 comes from a hand-built 17-trade scenario designed to trigger every rule; it
  says nothing about how much the rules would save a real trader.
- **The CSV layout is our assumption** (restated here because the counterfactual reads the same format): it is not a real broker's export format.
- **Not a subtraction.** A blocked trade is not applied, so later positions, realized P&L, loss streak and the order window differ and
  later decisions can change (the fixture has a trade that closes a long in the original and flips the account short with the rules).
  The report's impact is therefore the difference between two full sequential runs.
- **Fixture scale.** The hand-computed fixtures have 12 log fills / 14 broker rows and 17 trades. The property tests cover small
  random scenarios; reconciliation speed on large files was not measured.

## What the log can and cannot prove

- **Tail truncation is not detectable.** Each line's hash covers only the line before it, so deleting the newest N lines leaves
  a perfectly valid shorter chain. Nothing outside the file records how long the log should be. A test asserts this gap
  (`tests/test_chain.py::test_known_gap_tail_truncation_is_not_detected`). Closing it needs an external anchor, for example
  periodically publishing or signing the head hash; that is not built.
- **Edits followed by recomputing every later hash are not detectable either** for the same reason: the chain proves internal
  consistency, not authorship. It catches an edit, deletion or reordering that is not accompanied by a full rewrite.
- **The auditor needs the same `rules.yaml` as the gate.** The log does not record the rules in force, so auditing with a
  different file yields `DECISION_MISMATCH` findings or different P&L, and nothing in the log says which file was right.
- **Fees are not in the log.** They are recomputed from the fee schedule in `rules.yaml`, so a changed schedule changes the
  audited P&L of an old log.
- **Malformed requests are not logged.** Bad JSON, missing fields and floats get an error reply and leave no trace in the
  audit trail. Only well-typed orders (including REJECT/BAD_ORDER for an unknown symbol or non-positive qty/price) are logged.

## Durability

- **Headline fsync is plain `os.fsync`.** On macOS that hands data to the drive but does not ask the drive to flush its own
  cache, so an acknowledged order can still be lost on power failure. `--full-fsync` uses `F_FULLFSYNC`, which does. It is much
  more expensive on this machine: about 3,940 µs per call against about 19 µs in the microbenchmark, and in the 20k-order
  comparison `per_order` dropped to about 250–290 orders/s and pipelined `batched` to about 16k–39k orders/s (see PERFORMANCE.md).
  Neither was tested by cutting power.
- **`fsync = none` is not durable** and is labelled as such everywhere; it exists to show what fsync costs.
- **Only process crashes are tested** (`kill -9`, `tests/test_crash_recovery.py`). Power loss and OS crashes are not: a killed
  process leaves its `write()`s in the kernel, so those tests do not exercise fsync at all.
- **Torn tail policy.** A crash mid-write can leave a partial last line. On restart the gate removes just that partial line
  (it cannot have been acknowledged, since replies follow the completed write), saves the removed bytes to `<log>.torn.<ns>`
  and appends a note to `<log>.recovery.jsonl`. The note lives outside the hash chain, like the rest of the file's surroundings.
  Any other corruption makes the gate refuse to start. The auditor never repairs; it reports `partial last line`.
- **If an fsync fails, the gate stops** rather than acknowledge; its in-memory state is then ahead of the log, so it must be
  restarted from the log.

## Benchmark caveats

- **Docker on macOS runs in a Linux VM.** The Docker numbers' fsync cost reflects the VM's disk (ext4 on a virtual block device),
  not native macOS fsync, and how far a VM fsync reaches the host's drive was not measured. Host-to-container traffic goes through
  Docker Desktop's port forwarding, which adds latency. The VM's own CPU and RAM are a second limit on top of the container's
  (here the VM was larger, so the container limit bound).
- The load generator, gate and auditor share one machine; the native runs enforce no limits. Runs are single-machine, minutes long, not hours.
- Closed-loop `max` latency is one observation per run and was about 4–17 ms natively regardless of configuration; it is
  not interpreted.
- Pre-optimization (20k) and final (100k) native results are from different run sizes and are not directly comparable.

## Single node, single writer

One Python process, one writer thread, one log file; no replication, failover, or horizontal scale. Total order across all
symbols is what makes the log simple to verify, and it is also why throughput cannot be added with more processes without
changing that design.

## The next bottleneck (from the profile data)

The cProfile shares (native, pipelined, 8 connections, 100k orders; indicative, since profiling inflates Python time):

| Where the writer's time goes | `per_order` | `batched` |
|---|---:|---:|
| fsync | 39.4% | 1.1% |
| check / book / ledger code | 14.6% | 26.5% |
| orjson + pydantic (parse, validate, dump) | 11.3% | 21.9% |
| gate engine + server code | 9.6% | 16.2% |
| asyncio queues, streams, tasks | 9.3% | 16.9% |
| event loop core, socket reads, idle | 7.5% | 14.3% |
| log write syscall | 6.8% | 0.3% |
| sha256 hashing | 1.1% | 2.0% |
| socket send | 0.4% | 0.8% |

1. **With `per_order`, the bottleneck is fsync** (39% of the writer's time at about 34k orders/s with 66% gate CPU, unprofiled; the gate
   waits on the disk). `batched` removes it (group commit, same acknowledgement guarantee) and is the policy to use.
2. **With `batched`, the bottleneck is the single writer thread's CPU** (93–98% busy natively when pipelined). Its time is
   spread over order handling in Python — rules and ledger (27%), JSON/pydantic validation and dumping (22%), asyncio
   plumbing (17% + 14%), engine/server code (16%) — with no single hotspot, so the next gain means doing less Python work per
   order (for example skipping repeat pydantic validation on the trusted path) rather than tuning one function. Socket sends,
   previously the largest cost under `batched` (38% before the response-coalescing change), are now under 1%.
   Scaling past one core would require splitting the order stream, which gives up the single total order.
3. **Memory grows with log size and is the first hard limit under 512 MB.** The gate keeps every event it has seen (for
   idempotency) in memory and replays the whole log at startup: natively about 211 MB peak RSS at 100k events and about
   1,749 MB at 1M (6.2 s recovery); in the 512 MB container the 1M-event recovery was **OOM-killed**. Fixing this means keeping only
   `client_order_id -> seq` (or a bounded recent window) and checkpointing state instead of replaying everything.

## Copy-trading layer

- Replay only; no live data. yfinance serves 1m bars for about 7 days and 5m-30m for about 60 days (approximate, unofficial).
- The holiday/early-close table is hand-entered for 2024-2027 (outside it the calendar raises). Check it against the exchange's list.
- Strategies are illustrative, long-only, fixed-qty, and are not tuned or claimed to make money. Bars are IEX or Yahoo, not consolidated tape.
- The "tx index" in the chain ordering is 0 because `getTransaction` does not report it; ties within a slot fall to signature.
- Chain reading takes one page (up to 1000) of signatures per signer. Devnet memos are public; the local feed is not signature-verified.
- The devnet guard is a hostname check plus the cluster's genesis hash; it was not tested against a real mainnet node (tests use fakes).
- Publisher sends without confirming finality; a lost transaction is visible only as a gap in `leader_seq` on chain.

## Follower (`copier/`)

- **Paper only.** An accepted order is the gate's simulated full fill at the signal's `ref_price` (the leader bar's close); there is no slippage, latency or partial fill.
- **Price-band anchor** is the previous session's last 5-minute-bar close (IEX or Yahoo), not an official closing price. Prices that move more than the band from it over a day are rejected by `PRICE_BAND`; with the synthetic fixture the walk can drift past it.
- **Gate time is wall-clock.** A day of history replays in seconds, so `max_orders_per_minute` and the loss-streak cooldown (300 s) run on real elapsed time, not replayed time. Two live runs therefore have different gate `ts` and hashes; byte-identical logs are shown only with the gate's injected clock (tests).
- **A rejected or skipped BUY does not cancel the leader's later SELL.** The follower can then open an unintended short (seen in a demo run: a SELL that follows a MAX_DAILY_LOSS-rejected BUY). The gate still applies every limit, but the copier has no "only close what I hold" filter.
- **Last bar of the day:** a signal is stamped with its bar's end, so the closing bar ends exactly at 16:00. `MARKET_CLOSED` treats open and close as both tradable so a leader's closing exit can be copied; a real order at that instant would not fill.
- The leader's drawdown is its realized P&L from its own signals at `ref_price`, without fees, and it stops only orders that grow the follower's position. `FEED_GAP` blocks only the signal right after a gap.
- The local feed is not signature-verified; its single signer is `local`. Chain feeds are supported by the data model (`copier/feed.py`) but `make follow` runs the local feed only.
- Restart safety needs the same feed, config and rules: the copier recomputes its skips and refuses to continue if `copier.log` disagrees.
- Real-data fetching is exercised in tests only through fakes; yfinance gives about 60 days of 5m bars and may fail or rate-limit, in which case the labelled fixture is used.
