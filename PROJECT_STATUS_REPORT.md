# Trade Seatbelt: Project Status Report

Generated 2026-10-14 from the repository at `main` (HEAD `bb5c23f`, two uncommitted files). Intended to be handed to a fresh Claude session to plan the revamp.

**How this was produced.** I read `CLAUDE.md`, `README.md`, `Makefile`, `rules.yaml`, all `docs/*.md`, the core/gate/auditor/copier/leaders/sim/marketdata/api source headers and key bodies, the web pages, and the git log. I ran `pytest`: **345 passed, 1 skipped** (the skip is a Linux-only durability test), 26 s. I did **not** run `make demo`, `make bench`, the web build, `vitest`, or Playwright, and did not touch the network. Benchmark numbers below are quoted from `docs/PERFORMANCE.md`, not re-measured by me.

---

## 1. Executive summary

- **The original product (Risk Gate + Auditor) is essentially complete and well proven.** Hash-chained log, pure `check()` rules, single-writer asyncio gate with durable-before-reply, an independent auditor, broker-CSV reconciliation, counterfactual replay, crash-recovery tests, a reproducible benchmark with verification, and 20 documented invariants.
- **The project has drifted well beyond `CLAUDE.md`.** A *copy-trading* layer (market data, leader strategies, Solana devnet signal publishing, a follower/copier, a market simulator, run management) was added across ~8 commits. None of it is in `CLAUDE.md`, which says "Do not add features outside this file without asking" and describes a different audience. The landing page headline is now "Follow any trader, but never beyond your own limits." which is a different pitch from the CLAUDE.md one-liner.
- **The front end is half-rebuilt.** `/`, `/rules`, `/follow` are real. `/dashboard` and `/audit` are explicit placeholders ("built in the next step"). The audit page only has Run demo / Verify / Tamper.
- **Sponsor integrations (Gemini, ElevenLabs, Tiger Data) are not built.** Only `.env.example` placeholders exist. Solana, Alpaca and yfinance *are* used, which were never in the brief.
- **Docs are partly stale.** `docs/DISCLOSURE.md` still contains a "Not used: No external API or service is called" section above later sections that disclose Solana, Alpaca and yfinance. `README.md` still says `/dashboard` and `/audit` "are built in the next step".

---

## 2. Intended scope (from CLAUDE.md)

Hackathon: HackRU Fall 2026, Lucera Trading Systems Challenge. Judging: correctness and reliability 30%, system design and simplicity 25%, performance evidence 20%, completeness and demo 15%, trading relevance 10%.

One-liner: *Rules before the trade, receipts after.* A Risk Gate blocks orders that break the trader's own rules and writes a tamper-evident log; an Auditor replays that log, computes P&L, reconciles, and detects tampering.

Planned components: `gate/`, `auditor/`, `api/`, `web/`, `core/`, `bench/ tests/ fixtures/ docs/`. Optional: Gemini, ElevenLabs, Tiger Data. Definition of done: end-to-end demo, invariant tests green, benchmark table, LIMITATIONS naming the next bottleneck.

---

## 3. What exists, feature by feature

### 3.1 `core/`: shared kernel (done)

| File | What it does |
|---|---|
| `events.py` | Strict pydantic `Order` (floats/wrong types rejected, `client_order_id` regex `^[A-Za-z0-9_-]{1,64}$`) and `Event` (log line: `seq, ts, decision, reason_code, reason_text, prev_hash, hash` + order fields). Reason codes: OK, BAD_ORDER, KILL_SWITCH, RATE_LIMIT, PRICE_BAND, MAX_POSITION, MAX_DAILY_LOSS, LOSS_STREAK_COOLDOWN. |
| `canon.py` | Canonical JSON (sorted keys, no whitespace), `GENESIS`, `event_hash = SHA-256(prev_hash || canonical body)`. |
| `rules.py` | Rules schema (strict, frozen, `extra=forbid`) and the **pure `check()`**; no I/O, no clock. Fixed check order, first failure wins: unknown symbol/qty/price -> kill switch -> rate limit -> price band -> max position -> daily loss -> loss-streak cooldown. Daily-loss and cooldown only block *risk-increasing* orders, so a position can always be closed. Fee model: `max(qty*per_share, min_per_order)`. |
| `book.py` | All derived state: FIFO ledger, dedupe map (`seen`), 60 s order window, loss streak, cooldown. `verdict()` reads state, `apply()` mutates. Both gate and auditor use it. Rate-limit window is replayed from logged `ts`, so replay is deterministic. `state_hash()` for byte-identical replay checks. |
| `ledger.py` | FIFO lots, integer-only money, long/short flips, `identity_ok()` (`cash == gross - fees - open cost`), `snapshot()`. |
| `logio.py` | `LogWriter` with fsync policies (`per_order`, `batched`, `none`; optional macOS `F_FULLFSYNC`), verifying `scan()` (checks seq, prev_hash, hash, canonical form, partial last line), `repair_torn_tail()` (truncates only a partial final line, keeps the bytes in `.torn.<ns>`, notes in `.recovery.jsonl`). |
| `money.py` | Integer minor-unit formatting. |

### 3.2 `gate/`: Risk Gate (done)

- asyncio TCP server (uvloop), newline-delimited JSON, **single writer task** owning Book and log.
- **Group commit:** drains the queue into a batch, writes once, fsyncs once, then resolves replies. A reply is only sent after the fsync covering its event.
- **Idempotent:** duplicate `client_order_id` returns the original decision, nothing logged or counted (also after restart, since state is rebuilt from the log).
- Structurally malformed orders get `{"error": ...}` and are not logged; bad values (unknown symbol, qty/price <= 0) are logged as REJECT/BAD_ORDER.
- On start: repairs a torn tail, rescans and **refuses to start on any other corruption**.
- On a write failure the gate closes rather than diverging from its log.
- Optimization already applied: coalesce completed responses into one socket write (+40..148% pipelined throughput, measured).
- Injectable clock (used for deterministic tests). Dockerfile runs the gate only.

### 3.3 `auditor/`: Auditor (done)

- `replay.py`: verifies the chain, rebuilds state, **re-checks every logged decision with the same `check()`**. Findings: `DECISION_MISMATCH`, `DUPLICATE_ID`, `TIME_REGRESSION`. Reports the first bad seq and partial figures on a broken chain.
- `report.py`: JSON + Markdown report (counts, rejects by reason, FIFO P&L by symbol with fees, positions, cash, cash identity, head hash, state hash, scope disclaimer).
- `fills_csv.py`: strict broker-CSV parser (`ts, client_order_id, symbol, side, qty, price, fee`; integer minor units; `--lenient` collects all bad rows).
- `recon.py`: log vs broker. Categories `MATCHED, MISSING_IN_BROKER, MISSING_IN_LOG, QTY_MISMATCH, PRICE_MISMATCH, AMBIGUOUS`. Matches by id first, then by exact fields within a time tolerance; connected components so result is independent of CSV row order; ambiguity is never guessed. Reports implied position/cash difference.
- `counterfactual.py`: runs past trades sequentially through the gate's own `check()`/`Book` and reports what would have been blocked and the P&L impact (a sequential replay, not a subtraction).
- `explain.py`: **deterministic** plain-English explanation of every REJECT (and every copier skip) with numbers recomputed from a replay (position before/after, rate window, price deviation, daily loss, the losing streak behind a cooldown). Refuses a tampered log. No AI.

### 3.4 `client/`, `fixtures/`, `scripts/`

- `client/`: sends a fixture file to the gate.
- `fixtures/`: seeded order generator with **hand-computed expected totals** (`expected.json`), recon fixtures with hand-computed expected answers (12 log fills / 14 broker rows covering every category), counterfactual fixture, `tamper.py` (edit/delete/swap on a copy), synthetic 5-minute bars (labelled SYNTHETIC FIXTURE, not market data).
- `scripts/demo.sh` (generate -> gate -> client -> audit -> compare with expected -> 3 tamper checks), `demo_recon.sh`, `run_gate_docker.sh`.

### 3.5 `bench/`: benchmark harness (done, strongest "performance evidence")

Workload: 100k seeded orders over 32 symbols, ~84% accept, reference model computes exact expected totals. Closed-loop latency (avoids coordinated omission) and pipelined throughput at 1/8/32 connections x 3 fsync policies x 3 reps, load generator in separate processes. **Every run is verified** (chain, counts, auditor findings, per-order decision match); invalid runs are excluded. Also: recovery time vs log size (10k/100k/1M), cProfile breakdown, before/after of the one optimization, `os.fsync` vs `F_FULLFSYNC` comparison, native vs Docker (1 CPU / 512 MB, limits verified via `docker inspect` and cgroup files).

Headline numbers as reported in `docs/PERFORMANCE.md` (Apple M5, native, no limits):

| Setting | Closed-loop p50 / p99 | Throughput |
|---|---|---|
| batched, 1 conn | 84 µs / 102 µs | 11.5k orders/s |
| batched, 32 conn | 832 µs / 1.2 ms | 35k orders/s closed, ~107k pipelined |
| per_order, 8 conn | 365 µs / 440 µs | 21k closed, ~34k pipelined |
| Docker 1 CPU/512 MB, batched, 32 conn | 1.9 ms / 2.9 ms | 16k closed, ~63k pipelined |

Other measured facts: 353 bytes/event; auditor replay ~157k events/s; 1M-event recovery ~6.2 s and 1.7 GB RSS; `F_FULLFSYNC` costs ~208x per call (3.9 ms vs 19 µs) and cuts per_order throughput ~98%. Honest caveats are written in the doc (p99.9 rests on ~94 samples, Docker fsync reflects the VM disk, process-crash vs power-loss distinction).

### 3.6 `api/`: FastAPI (done, read-mostly, 127.0.0.1 only)

| Route | Purpose |
|---|---|
| `GET /health`, `GET /report` | health; audit report of the demo log |
| `GET /events/stream` | SSE tail of the log |
| `POST /demo/run`, `GET /demo/status` | runs the demo flow with its own gate in a temp dir |
| `POST /tamper` | edits a **copy** of the log, verifies the copy, returns first bad seq (no client-supplied paths) |
| `GET/POST /recon`, `GET/POST /counterfactual` | bundled fixtures, or user CSV as raw body (1 MB cap, errors name the row) |
| `GET /bench`, `GET /invariants` | stored bench numbers; invariant list plus test status read only from the JUnit file |
| `/rules` GET/PUT (`rules_profile.py`) | validated rules profile saved to `profile/rules.json` (gitignored; repo `rules.yaml` never modified) |
| `/follow`: `GET /leaders`, `POST /run`, `GET /{run}/summary`, `POST /{run}/stop`, `GET /{run}/explain/{n}`, `GET /{run}/stream` | starts a follower subprocess, streams rows, explains a row |
| `/runs` (create, list, get, select, pause, resume, stop, speed, `events/stream`, `report`) | simulator run manager |

### 3.7 `sim/`: market simulator (extra, not in CLAUDE.md)

Headless simulated trader as a normal TCP client of the gate. Price model = GBM per symbol (sigma 0.30, no drift). Behaviour profiles `disciplined`, `impulsive`, `tilt` (revenge trading) with documented knobs (oversize, off-market, fat-finger digit slips, bursts). Price anchors from yfinance, Alpaca (read-only, keys optional, tested only against a fake HTTP client) or built-in offline prices. Runs live in `runs/<id>/`. `make sim`. Exposed via `/runs` API. **No front-end page uses it.**

### 3.8 Copy-trading layer (extra, not in CLAUDE.md)

- `marketdata/`: `Bar` / `BarSet` types (integer cents, epoch ms, ts = bar end so no look-ahead), NY trading calendar, replay backend with on-disk cache (no data means `NoDataError`, never invented), Alpaca and yfinance providers, disabled stubs for live Alpaca / xStocks / Pyth (make no network calls).
- `leaders/`: three long-only strategies (`orb` opening-range breakout, `momentum`, `meanrev`) fed one bar at a time with a look-ahead guard; signal format v1 (strict, <= 400-byte memo; see `docs/SIGNALS.md`); `solana.py` Ed25519-signed Memo transactions on **devnet only** (refuses other clusters, throwaway key in gitignored `.keys/`); `publisher.py`, `feed.py` (chain reader with retry on 429, cache, local fallback); `python -m leaders setup|devnet-check`.
- `copier/`: the follower. Fixed-order pre-filters (INVALID_SIGNAL, UNTRUSTED_LEADER, DUPLICATE_SIGNAL, UNKNOWN_SYMBOL, MARKET_CLOSED, STALE_SIGNAL, FEED_GAP, LEADER_DRAWDOWN_STOP, SELL_WITHOUT_POSITION), integer scaling or fixed qty, close-only mode (SELL clipped to long position), session limits (per-trade amount, trade count, volume, budget; the last three end the session), seeded pace (timing only), deterministic `client_order_id` from signal id, its own hash-chained `copier.log`, restart-safe. Then the order goes through the **real gate**. `make follow`, `make follow-chain`.
- Fallback chain for data: chain -> cache -> local feed -> labelled SYNTHETIC FIXTURE, with the source stated in output and manifest.

### 3.9 `web/`: Vite + React 19 + TS (partial)

Stack: plain CSS on design tokens, fontsource fonts, three.js + @react-three/fiber + drei (lazy chunk per page), vitest, Playwright e2e scripts (`shots.mjs`, `follow.mjs`, `size.mjs`). Dark minimal theme, 2D fallback for phones / reduced motion / no WebGL.

| Page | State |
|---|---|
| `/` Landing | **Done, being polished.** Animated 3D five-step flow with real SHA-256 hashes (labelled illustration), "How Solana is used / does not do", example rejection, "What you can check". Uncommitted edits to `Landing.tsx` and `index.css` (section padding, hover and pulse animations). |
| `/rules` | **Done.** Presets Cautious / Balanced / Active or edit four limits; validated and saved server-side; follow is refused (409) until saved. |
| `/follow` | **Done.** Pick leader, source (Auto / Chain devnet / Local), copy size, session limits, pace; live rows with a block strip, drawer and plain-English "why". |
| `/dashboard` | **Placeholder.** Mini chain of recent demo decisions plus a "built in the next step" sentence. No P&L, positions, reject breakdown or charts (uPlot is noted but not installed). |
| `/audit` | **Mostly placeholder.** Single "Integrity" tab: Run demo, Verify log, Tamper-a-copy, with the medium 3D chain. Missing: log table, rejections, report/recon/counterfactual views, Solana links, export. |

Client libs: `store.ts` (SSE + demo/verify/tamper state), in-browser SHA-256 chain recomputation (`hash.ts`, tested), money formatting (tested), follow client.

### 3.10 Tests (345 pass)

Counts of test functions per file: copier 30, leaders 31, sim 28, api 32, recon 23, fills_csv 11, gate 10, runs_api 10, follow_api 9, chain 9, counterfactual 9, durability 5, crash_recovery 5, rules 3, ledger 2, ratelimit_replay 1, web_build 1 (skipped unless `npm ci` done). Hypothesis property tests cover: no ACCEPT breaches limits (random streams and 8 concurrent clients), FIFO identity after every fill, explanation numbers equal independent replay, follower accepted orders satisfy limits. Includes negative controls (e.g. a gate that replies before fsync is flagged; tail-truncation gap is asserted, not hidden) and real `kill -9` crash tests. Front-end tests exist in `web/src/lib/*.test.ts` (not run by me).

### 3.11 Docs

`INVARIANTS.md` (20 rows: claim, tests, command), `LIMITATIONS.md` (names next bottleneck), `PERFORMANCE.md` (378 lines, full method), `DISCLOSURE.md` (libraries with licenses read from metadata, Claude Code usage), `SIGNALS.md`, `README.md`, MIT `LICENSE`.

---

## 4. Workflows

1. **Core demo** (`make demo`): generate seeded fixture -> start gate (batched fsync) -> client sends orders -> SIGTERM gate -> auditor writes `out/demo/report.{md,json}` -> compare against hand-computed totals -> tamper three copies (edit / delete / swap) and show first bad seq.
2. **Recon demo** (`make demo-recon`): auditor with broker CSV and counterfactual on generated fixtures.
3. **Follow flow** (`make follow` or the web UI): rules saved -> leader strategy replays bars -> signals (local `feed.jsonl` or Solana devnet memos) -> copier pre-filters -> TCP to a private gate -> gate checks the follower's rules and logs -> `copier.log` + `follow.summary.json` -> explanations per row.
4. **Web flow:** `make api` + `make web`; Landing -> Rules -> Follow -> (Dashboard) -> (Audit).
5. **Benchmark:** `make bench` (native), `make bench-docker`, `make bench-report FINAL=... [DOCKER=...]` (only this writes the committed `docs/PERFORMANCE.md`).
6. **Tests:** `make test` (also writes the JUnit file the landing page / `/invariants` read).

---

## 5. Gaps, risks and inconsistencies

**Scope and product direction**
1. Copy-trading pivot is undocumented in `CLAUDE.md`, and contradicts its "keep it small / ask first" rule and its audience list. The brief's judging criteria reward simplicity and trading relevance; the layer adds ~5 packages, Solana, and 3 external data sources. Decide: keep as the headline, demote to an optional extra, or remove.
2. Core "Trade Seatbelt" story (gate + auditor + receipts) is now secondary on the landing page.

**Missing / unfinished**
3. `/dashboard` and `/audit` are stubs: this is the largest completeness hole for the 15% demo criterion.
4. Gemini, ElevenLabs, Tiger Data unbuilt (optional, but listed in the brief; the interfaces and disabled-by-default flags do not exist in code either).
5. No UI for the simulator or reconciliation / counterfactual, even though the APIs exist.
6. Dashboard charts library (uPlot) not installed.

**Correctness / trust**
7. Tail truncation of the log is undetectable (documented and tested). An external anchor (e.g. periodically publishing the head hash, possibly to devnet, or a signed checkpoint) is the natural fix.
8. Gate `ts` is wall-clock, so two live runs never hash identically (deterministic only with the injected clock).
9. Durability claims are process-crash only; default `os.fsync` on macOS does not survive power loss (`F_FULLFSYNC` is opt-in and ~208x slower per call).
10. Real network paths (Solana devnet, Alpaca live) were never exercised by the test suite; Alpaca only against a fake client.
11. Auditor needs the same `rules.yaml` as the gate (fee schedule is not in the log), so a changed rules file silently changes audited P&L. Recording a rules hash in the log would close this.
12. Simulator price model is a toy and static reference prices drift outside the band on long runs (documented).

**Docs / hygiene**
13. `DISCLOSURE.md` has a stale "Not used: no external API or service is called" section that conflicts with later sections. `README.md` says dashboard/audit "are built in the next step". `docs/PERFORMANCE.md` cites commit `fd47adb`, a pre-rebase hash; `bench/commit_map.json` maps it.
14. `copier/pace.py` is described as making activity "never look instant or machine-regular". For a paper simulator it is harmless, but that wording reads like evasion of bot detection; reword to "so the UI is readable" to avoid a bad impression.
15. Working tree: uncommitted `Landing.tsx` / `index.css` edits; `runs/`, `out/`, `profile/` present locally (the first two should be gitignored; I saw `.env`, `.keys`, `.venv` are ignored; I did not verify `runs/`, `out/`, `profile/`, so check before pushing).
16. `typescript ^7.0.2`, `vite ^8`, `react ^19.3` and similar are recent major versions; confirm `make web` works from a clean clone (`npm ci`), since the CLAUDE.md requires all five make targets from a clean clone and I did not test that.

---

## 6. Revamp considerations (for the next conversation)

Questions to settle first:
- **Positioning:** is the product "Risk Gate + Auditor for any trader" (CLAUDE.md) or "Safe copy-trading" (current site)? Everything else follows from this.
- **Keep or cut** Solana/leaders/copier/sim/marketdata. If kept, update `CLAUDE.md`; if cut, ~40% of the Python and most of the landing copy can go.
- **Which sponsor integrations** are worth the time, given the offline-first rule.

Candidate work, in rough priority (my suggestion, not decided):
1. Align `CLAUDE.md`, README and DISCLOSURE with reality (cheap, protects the "honest" criterion).
2. Finish `/audit` (log table, reject breakdown, report, recon and counterfactual views, export) and `/dashboard` (P&L, positions, rejects by rule, live stream); these use APIs that already exist.
3. Verify a clean-clone run of `make setup/demo/test/bench/web`, and re-run `web-test` and the Playwright checks.
4. Close the tail-truncation gap with a head-hash checkpoint, and record the rules hash in the log.
5. Optional integrations behind interfaces: deterministic ElevenLabs alert text, Gemini explanation fed only structured facts and citing seq numbers, Tiger Data sink reading the log.
6. Commit or drop the pending landing polish.

## 7. Repository map

```
core/ gate/ auditor/ client/        original product
api/                                FastAPI (report, SSE, demo, tamper, recon, counterfactual, bench, invariants, rules, follow, runs)
sim/ marketdata/ leaders/ copier/   added: simulator, market data, signal publishing (Solana devnet), follower
web/                                Vite + React + TS + three.js; 5 routes (2 are placeholders)
bench/ fixtures/ scripts/ tests/    harness + results, generated fixtures, demo scripts, 345 tests
docs/                               INVARIANTS, LIMITATIONS, PERFORMANCE, DISCLOSURE, SIGNALS
Makefile                            setup demo demo-recon test bench bench-docker bench-report api web web-test sim follow follow-chain devnet-check leaders-setup
```

Git history (oldest to newest): initial -> Stage 1 core/gate/auditor -> bench -> durability/crash recovery -> recon + counterfactual -> API -> 3D dashboard -> landing + invariants endpoint -> flaky-test fix -> simulator -> market data/leaders/devnet memos -> copier -> follow API -> UI rebuild (Tier A) -> dark minimal restyle -> landing centring.
