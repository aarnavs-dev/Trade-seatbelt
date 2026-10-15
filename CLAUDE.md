# Trade Seatbelt: project context (read this first, every session)

Safe copy-trading with verifiable history. You follow a strategy (a "leader"); every copied order passes through **your own rules**
in a Risk Gate before anything happens; every decision leaves a tamper-evident receipt that an independent Auditor can check.
Paper trading only for now. The hackathon (HackRU Fall 2026) is over; its judging criteria no longer drive scope.

`PLAN.md` is the roadmap and the source of scope. `PROJECT_STATUS_REPORT.md` is a snapshot analysis from 2026-10-14. If this file and the
code disagree, the code wins: fix this file.

## Who it is for (use this language in copy)
- People who want to follow a trading strategy without trusting it blindly or watching every order.
- Traders who lose money by breaking their own rules: oversized positions, revenge trading, fat-finger prices.
- Anyone who needs proof of what was decided and why, independent of whoever ran the system.

## Decisions (settled)
- Paper trading now; a real broker only at product release, behind an order-routing interface.
- Leaders are strategies we publish and run ourselves, not strangers on chain.
- Mainnet is undecided. Production must stay free, so everything must work with no paid chain access.
- UI default anchor is Local; devnet is an opt-in "publish". Checkpoints: every N events or 5 minutes, whichever first, plus one at session end.

## Principles
1. **Offline-first.** Gate, copier and auditor work with no network and no keys. Solana is a feature, never a dependency.
2. **Honest labelling.** Simulated or synthetic data is always labelled (`SYNTHETIC FIXTURE`, "paper", "devnet"). Docs state limits, including what an anchor does not prove.
3. **Hashes only on chain.** Never put order data on chain.
4. **Deterministic explanations.** The auditor's plain-English "why" has no AI. Any optional AI layer gets structured facts only, must cite seq numbers, can never change a decision, number or report, and is labelled "AI-generated".
5. **Routing behind an interface** so a broker adapter never means touching the gate.
6. **Describe the repo as it is.** Verify before writing docs. Never claim a number you have not measured; under-claim, over-prove.

## Architecture
Source of truth = the append-only hash-chained log. Everything else is a derived view and must be optional.

| Package | Role |
|---|---|
| `core/` | Shared kernel: event schema, canonical JSON + hash chain, pure `check()` rules, `Book` (all state derived from events), FIFO integer ledger, log reader/writer. Gate and auditor both import it. |
| `gate/` | Risk Gate. asyncio TCP (uvloop), newline-delimited JSON, **single writer**, group commit, reply only after the covering fsync, idempotent on `client_order_id`, torn-tail repair, refuses to start on a corrupted log. Hot path. |
| `auditor/` | Separate process, deterministic. Chain verify, replay that re-checks every decision, FIFO P&L with fees, JSON + Markdown report, `fills_csv` + `recon` (broker CSV), `counterfactual`, `explain` (deterministic reject explanations). Needs the same `rules.yaml` as the gate. |
| `marketdata/` | Replay backend over Alpaca / yfinance bars, on-disk cache, NY calendar. No data = `NoDataError`, never invented. Live backends are disabled stubs. |
| `leaders/` | Long-only strategies (`orb`, `momentum`, `meanrev`), signal format v1 (`docs/SIGNALS.md`), Ed25519-signed Solana **devnet** memos (refuses other clusters), chain feed reader. |
| `copier/` | The follower. Fixed-order pre-filters, scaling, close-only mode, session limits, seeded pace, its own hash-chained `copier.log`. A normal TCP client of the gate. |
| `sim/` | Simulated trader (GBM prices, `disciplined`/`impulsive`/`tilt` profiles) as a gate client; run manager. |
| `api/` | Thin FastAPI on 127.0.0.1: report, SSE streams, demo, tamper-on-a-copy, recon, counterfactual, bench, invariants, rules profile, follow runs, sim runs. **Never on the order hot path.** |
| `web/` | Vite + React 19 + TS, plain CSS on design tokens, three.js (lazy) with a 2D fallback. |
| `bench/ tests/ fixtures/ scripts/ docs/` | Benchmark harness and stored results, tests, generated fixtures, demo scripts, docs. |

## Event format and rules
Event: `seq, ts, client_order_id, symbol, side, qty, price, decision(ACCEPT|REJECT), reason_code, reason_text, prev_hash, hash`.
`hash = SHA-256(prev_hash || canonical JSON of the rest)`. Canonical JSON = sorted keys, no whitespace, integers in minor units, **no floats for money**.
**Log v2 (Phase 2, done):** besides order events a log may hold *control records*, chained lines with their own `type`. The only one so far is
`{type: "RULES", seq, ts, log_version: 2, rules_hash, prev_hash, hash}`, written by the gate at start-up whenever the SHA-256 of the canonical JSON of its
*parsed* rules differs from the last recorded one (rules are loaded once per process, so one record per start is enough; it is durable before the first
order is accepted). It takes a seq, so in a v2 log the first order is seq 2. Order events are byte-identical to v1, so a v1 log (no record) verifies unchanged
and is reported `NOT_RECORDED`. `core.logio.scan()` yields orders only (the chain, records included, is still fully verified); `scan_records()` yields
both. Anything that reads a log line by line must skip or handle `type` lines (the SSE tail sends them as a `control` event, `copier/rows.py` skips them).

`rules.yaml`: `max_position_per_symbol`, `max_daily_loss`, `price_band_pct` (vs reference price), `max_orders_per_minute`, `loss_streak_cooldown`, `kill_switch`, `fees`.
`check()` order is part of the contract (first failure wins): bad order, kill switch, rate limit, price band, max position, daily loss, loss-streak cooldown.

## Invariants
Listed with their tests in `docs/INVARIANTS.md` (21 rows). Each needs a test that tries to break it. Headline ones: no ACCEPT breaches a limit; replay gives a byte-identical state hash; cash and positions reconcile with integer money; duplicate ids are never double-counted; any edit/delete/reorder of a log line is detected and the first bad seq reported; reconciliation never guesses (AMBIGUOUS / MISSING_IN_BROKER / MISSING_IN_LOG); no reply before its fsync; kill -9 loses no acknowledged order; the rules a log ran under are on record inside the chain and the auditor reports drift (`RULES_MISMATCH`).

## Decisions baked into the code
- ACCEPT is an immediate full fill at the order price (simulation).
- Structurally malformed orders (bad JSON, missing field, wrong type, floats) get `{"error":...}` and are not logged. Valid-typed, bad-valued orders (unknown symbol, qty/price <= 0) are logged as REJECT/BAD_ORDER.
- Daily-loss and cooldown block only risk-increasing orders, so a position can always be closed.
- The kill switch is a `rules.yaml` flag only; there is no runtime control message.
- The follower is close-only unless `allow_short`; its `client_order_id` is derived from the signal id.
- A follow run uses a copy of the saved rules profile (`profile/rules.json`, gitignored); the repo's `rules.yaml` is never modified.
- Tail truncation (deleting the newest lines) is not detectable by the chain alone; a test asserts the gap. Phase 3 closes it up to the last checkpoint.
- Tests that need exact `ts` or seq values must allow for the rules record: the gate reads its clock once at start-up for it (`tests/helpers.clock_from`), and `Gate(..., record_rules=False)` writes a v1-style log (used for the committed recon fixture, which is deliberately v1).
- The audit report has a `rules_record` section (`status` MATCH / MISMATCH / PARTIAL / NOT_RECORDED), and `chain.records_verified` counts orders plus records; `counts` and `chain.events_verified` count orders only.

## Current state
Verified from a clean clone of `bb5c23f` on 2026-10-15 (before Phase 2): `make setup`, `demo`, `demo-recon`, `test`, `web-test`, `web-build`, `bench` (20k x1, all valid), `api`, `web`, `shots.mjs`, `size.mjs`.
After Phase 2, in the working tree: `make test` 398 passed, 1 skipped (Linux-only); `demo` (now also tampers the rules record), `demo-recon`, `web-test`, `web-build`, `shots.mjs`, and a 20k x1 `bench` (all runs valid) pass. The committed `docs/PERFORMANCE.md` predates the rules record and has not been re-run.
Not working or not built:
- `/dashboard` and `/audit` are **placeholders** (mini chain; Run demo / Verify / Tamper only).
- No UI for the simulator, reconciliation or counterfactual (their APIs exist).
- Not built: Gemini, ElevenLabs, Tiger Data, anchors/checkpoints, OpenTimestamps, broker adapter.
- Never exercised against the real network: Solana devnet (`make devnet-check`), live Alpaca (tested only against a fake client).
- The initial bundle is large (`Scene` chunk ~930 kB raw, ~248 kB gzip; Vite warns). It loads lazily on the 3D pages.

## Known gaps (document or close)
Tail truncation undetectable; default macOS `os.fsync` does not survive power loss (`F_FULLFSYNC` is opt-in, ~208x slower per call); gate `ts` is wall-clock so live runs never hash identically (deterministic only with the injected clock); the auditor still needs the same `rules.yaml` as the gate (the log holds only its hash, so drift is now reported but the rules themselves are not recoverable); the simulator price model is a toy and static reference prices drift out of the band on long runs.

## Hard rules
- Simulated/paper only until the broker phase. No real orders, no real money, no broker credentials in the repo.
- No secrets in the repo. `.env` and `.keys/` are gitignored; `.env.example` lists placeholders. No personal financial data. Fixtures are generated.
- Solana writes refuse any non-devnet URL until a mainnet decision is made and documented.
- Benchmarks must be reproducible with one command; report only measured numbers. `make bench` never touches the committed `docs/PERFORMANCE.md`; only `make bench-report` does.
- Landing copy must be honest: say "simulated", say what is and is not covered.
- Optional integrations (Gemini, ElevenLabs, Tiger Data) live behind interfaces, off by default, and disclosed in `docs/DISCLOSURE.md` (also update it whenever a dependency or external service is added).
- The log format (Phase 2) is settled: do not change event fields, hashing or the record layout casually. New control record types (Phase 3 checkpoints) go through `CONTROL_TYPES` in `core/events.py`.

## Stack
Python 3.12, uvloop, orjson, pydantic v2, pytest, hypothesis, FastAPI. Frontend: Vite, React, TypeScript, plain CSS on design tokens, three.js + @react-three/fiber + drei (no Tailwind, no framer-motion). Docker only for benchmark limits (1 vCPU, 512 MB).

## Commands (all verified from a clean clone)
`make setup`, `make demo`, `make demo-recon`, `make test`, `make bench` (`BENCH_N=20000 BENCH_REPS=1` for a quick run), `make bench-docker`, `make bench-report FINAL=...`, `make api`, `make web`, `make web-test`, `make web-build`, `make sim`, `make follow`, `make follow-chain`, `make leaders-setup`, `make devnet-check`.
Browser checks (with `make api` and `make web` running): `node web/e2e/shots.mjs`; with `npx vite preview --port 4173`: `node web/e2e/size.mjs`.
`make test` writes `out/pytest-junit.xml`, which the landing page and `/invariants` read for test status.

## Working agreements
- Commit after each meaningful step. Keep commits small; stage specific files (the tree may hold unrelated edits).
- Run `make test` before committing code; run the Playwright check before committing UI.
- Phases and their order are in `PLAN.md`. Do not start a later phase without being asked; do not add features outside the plan without asking.
