# Limitations

What this project does not do, does not guarantee, or has not shown. Measured figures come from
[PERFORMANCE.md](PERFORMANCE.md) and `bench/results/*.json`.

## Scope

- **Simulated only.** No real orders, money, broker or market data. An ACCEPT is an immediate full fill at the order's own
  price: no slippage, partial fills, queue position or rejects from a venue.
- **Reference prices are static** values in `rules.yaml`; the price band is measured against them, not a live market.
- **No** stock splits or other corporate actions, wash-sale or tax-lot rules, short-borrow costs, FX or multiple currencies.
  P&L is FIFO, in integer cents, with a per-share fee that has a per-order minimum.
- **Broker reconciliation is not built** (invariant 6 in [INVARIANTS.md](INVARIANTS.md) has no test yet). The brief's `api/`,
  `web/` and optional Gemini / ElevenLabs / Tiger Data integrations are not built either.
- **Rate limit, daily loss and cooldown** are covered by unit and replay tests, not by the benchmark (it disables the first two
  and exercises the cooldown only in a short epilogue).

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
