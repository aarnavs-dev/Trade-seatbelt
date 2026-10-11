# Trade Seatbelt — project context (read this first, every session)

HackRU Fall 2026, Lucera Trading Systems Challenge. Judged on: correctness & reliability 30%, system design & simplicity 25%, performance evidence 20%, completeness & demo 15%, trading relevance 10%. Visual polish alone does NOT decide results. Under-claim, over-prove.

## One-liner
Rules before the trade, receipts after. A Risk Gate blocks orders that break the trader's own rules and writes a tamper-evident log. An Auditor replays that log, computes P&L, reconciles, and detects tampering.

## Who it is for (use this language on the landing page)
- Intraday traders with high order volume who cannot manually log and audit every trade.
- Traders who suffer large losses from rule-breaking: oversized positions, revenge trading, fat-finger prices.
- Anyone whose broker statement and own records disagree and who needs proof of what happened.

## Architecture (keep it this small)
1. `gate/`  Risk Gate. asyncio TCP server, newline-delimited JSON, single writer, sequence numbers. Hot path. Pure `check()` rules function, no I/O inside it.
2. `auditor/` Auditor. Separate process. Reads log, verifies hash chain, rebuilds positions/cash, FIFO P&L with fees, reconciliation against a broker CSV, JSON + Markdown report. Deterministic.
3. `api/` Thin read-only FastAPI for the dashboard (SSE stream of events, report endpoint, counterfactual endpoint). NEVER on the order hot path.
4. `web/` Vite + React + TypeScript + react-three-fiber. Landing page + live dashboard.
5. `core/` Shared: event schema, hash chain, rules, FIFO ledger. Gate and auditor both import it.
6. `bench/`, `tests/`, `fixtures/`, `docs/`.

Source of truth = the append-only hash-chained log file. Everything else (DB, UI, AI, voice) is a derived view and must be optional.

## Event format
`seq, ts, client_order_id, symbol, side, qty, price, decision(ACCEPT|REJECT), reason_code, reason_text, prev_hash, hash`. hash = SHA-256(prev_hash || canonical JSON of the rest). Canonical JSON = sorted keys, no whitespace, integers in minor units (no floats for money).

## Rules (rules.yaml)
max_position_per_symbol, max_daily_loss, price_band_pct vs reference price, max_orders_per_minute, loss_streak_cooldown, kill_switch (halt all).

## Invariants (each needs a test that tries to break it; list in docs/INVARIANTS.md)
1. No ACCEPTED order ever breaches a configured limit (property-based, Hypothesis, random streams, concurrent clients).
2. Replaying the same log gives a byte-identical state hash.
3. Cash + positions reconcile after every fill; no float money.
4. Duplicate client_order_id returns the original decision and never double-counts.
5. Any edit, deletion or reordering of a log line is detected and the first bad seq is reported.
6. Ambiguous or missing evidence in reconciliation is reported as UNKNOWN, never guessed.

## Stack decisions
Python 3.12, uvloop, orjson, pytest, hypothesis, FastAPI. Frontend: Vite, React, TS, @react-three/fiber, drei, framer-motion, Tailwind. Docker for benchmark limits (1 vCPU, 512 MB).

## Optional sponsor integrations (all behind interfaces, all disabled by default, app must run fully offline without keys)
- Gemini API: explains a REJECT or an audit finding in plain English. Input = structured facts only (event fields, rule, limit). Output must cite event seq numbers. AI can never change a decision, a number, or a report. Show "AI-generated, facts above are deterministic".
- ElevenLabs: speaks a short alert/summary ("Order blocked: would exceed max position 100"). Text is generated deterministically from the event, not by the LLM.
- Tiger Data (TimescaleDB): read-model for events/fills as hypertables with continuous aggregates (P&L by hour, rejects by rule). Written asynchronously by a separate sink reading the log. If unavailable, the dashboard falls back to reading the log. Never in the hot path.
Disclose all three in docs/DISCLOSURE.md.

## Hard rules
- Simulated/paper only. No real orders, no real money, no broker credentials.
- No secrets in the repo. Use .env (gitignored) and .env.example. No personal financial data. Fixtures are generated.
- Never claim a number you have not measured. Benchmarks must be reproducible with one command.
- Keep it small. Do not add features outside this file without asking.
- Landing page copy must be honest: say "simulated", say what is and is not covered.

## Commands that must always work from a clean clone
`make setup`, `make demo`, `make test`, `make bench`, `make web`

## Definition of done
Demo runs end to end (client -> gate -> log -> auditor -> report), all invariant tests green, benchmark table in docs/PERFORMANCE.md (environment, workload, metric, procedure, interpretation), docs/LIMITATIONS.md names the next bottleneck.

## Stage 1 decisions
- ACCEPT is an immediate full fill at the order price (simulation).
- Structurally malformed orders (bad JSON, missing field, wrong type, floats) get an `{"error":...}` reply and are not logged. Valid-typed but bad-valued orders (unknown symbol, qty/price <= 0) are logged as REJECT/BAD_ORDER.
- Daily-loss and cooldown rules only block risk-increasing orders, so a position can always be closed.
- The kill switch is a `rules.yaml` flag only; there is no runtime control message.
- The auditor needs the same `rules.yaml` as the gate (the log does not record the fee schedule).
- Tail truncation (deleting the newest lines) is not detectable by the chain alone. It is documented, and a test asserts the gap.
