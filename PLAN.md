# Trade Seatbelt: Plan

Written 2026-10-15, revised the same day. Based on `PROJECT_STATUS_REPORT.md` (generated 2026-10-14 from `main`, HEAD `bb5c23f`).

## Direction

Safe copy-trading with verifiable history. You follow a strategy ("leader"), every copied order passes through your own rules in the Risk Gate first, and everything leaves receipts that can be checked independently. Anchors (local first, then Solana and others) provide a tamper-resistant record.

The hackathon is over. Scope is no longer limited by the old judging criteria or the old `CLAUDE.md`.

## Decisions made

| Question | Decision |
|---|---|
| Paper trading or real broker? | Paper trading now. Broker later, at product release. |
| Who are the leaders? | Strategies we publish and run ourselves, not strangers on chain. |
| Mainnet? | Undecided. Production must stay free, so the app must work fully without paid chain access. |
| Default anchor shown in the UI | Local. Devnet is an opt-in "publish". |
| Checkpoint timing | Every N events or 5 minutes, whichever comes first, plus one at session end. Event count alone is bad for quiet sessions. |

## Principles

1. **Offline-first.** The gate, copier and auditor work with no network. Solana is a feature, never a dependency.
2. **Honest labelling.** Simulated or synthetic data is always labelled. Docs state limits, including what an anchor does not prove.
3. **Hashes only on chain.** Never put order data on chain.
4. **Deterministic explanations.** Keep the auditor's plain-English "why" free of AI. Any optional AI layer may only be fed structured facts and must cite seq numbers.
5. **Routing behind an interface.** So a broker adapter later does not mean touching the gate.
6. **Describe the repo as it is.** Verify first, then write docs.

## Already built (keep)

- Risk Gate: hash-chained log, durable-before-reply, idempotent, crash recovery.
- Auditor: replay, report, broker-CSV recon, counterfactual, deterministic reject explanations.
- Benchmark harness, 20 invariants, 345 passing tests (1 skipped).
- Market data, three long-only leaders (orb, momentum, meanrev), signed Solana devnet memos, copier.
- Simulator with disciplined / impulsive / tilt profiles.
- Pages: `/`, `/rules`, `/follow`.

## Phases

### Phase 1: Foundations (commit after each step)
1. [x] **Clean-clone verification first.** Done 2026-10-15 from a fresh clone of `bb5c23f`: `make setup`, `demo`, `demo-recon`, `test`, `bench` (20k x1), `web`, `web-test`, `web-build`, `shots.mjs`, `size.mjs` all pass. Only breakage: `web/e2e/follow.mjs` tested the removed `/app/follow` page; deleted (commit `0b1b64e`).
2. [x] Check `runs/`, `out/`, `profile/` are gitignored (only `.env`, `.keys`, `.venv` were confirmed). `runs/`, `out/`, `profile/` and the other generated dirs are all ignored and untracked. Landing edits verified (build, `shots.mjs`) and committed (`ef7ce9a`).
3. [x] Fix `README.md` (dashboard and audit are still placeholders, so the README now says so rather than "next step") and `docs/DISCLOSURE.md` (remove the stale "Not used: no external API or service" section). Reword the `copier/pace.py` description (timing so the UI is readable, not "never look machine-regular").
4. [x] Rewrite `CLAUDE.md` for the copy-trading product, based on this plan and on what step 1 found.

### Phase 2: Log format change (before any anchoring)
Recording the rules hash changes the event format and hashes. Do it once, deliberately.
- [ ] Add a **log version field** so old logs still verify.
- [ ] Record the **rules hash** in the log, so a changed `rules.yaml` cannot silently change audited P&L.
- [ ] Update everything that depends on the format: `core/events.py`, fixtures' `expected.json`, the benchmark reference model, recon and counterfactual fixtures, invariant tests, `docs/INVARIANTS.md`.
- [ ] Re-run the full suite and a verified benchmark; confirm old (v1) logs still verify.

### Phase 3: Checkpoints and the anchor interface (local first)
- [ ] Anchor interface with a **local backend** first. No network needed.
- [ ] Checkpoint = `seq`, `head_hash`, rules hash. Timing: every N events or 5 minutes, whichever comes first, plus one at session end.
- [ ] Auditor truncation check: flags a log shorter than the latest checkpoint, or a hash mismatch; shows "no anchor available" when there is none.
- [ ] Truncation tamper demo showing the anchor catching what the chain alone cannot.
- [ ] Honesty line in `docs/LIMITATIONS.md` and in the UI: **an anchor shows the log existed up to that seq at that time. It does not show the log is complete or that the trades were sensible.** Also: truncation after the last checkpoint is still undetectable.

### Phase 4: Network anchor backends
Added behind the same interface, after the local path is proven.
- [ ] Solana devnet (free, can be reset, not permanent). Run `make devnet-check` against the real network, which has never been exercised.
- [ ] Fake-RPC tests: 429 rate limits, timeouts, partial failures. Retry/backoff and clear offline behaviour.
- [ ] Key handling: where the key lives, how it is generated, what happens if it is lost.
- [ ] OpenTimestamps (free, Bitcoin-anchored, slower to confirm).
- [ ] Solana mainnet (optional, paid by whoever opts in). Decision deferred.

### Phase 5: Leaders and broker interface
- [ ] Trend follower leader (moving-average crossover).
- [ ] Buy-and-hold baseline leader (control: does any strategy beat doing nothing?).
- [ ] **Reckless leader** (oversized, fat-finger, revenge-style trades, reusing simulator `tilt` ideas). Demo star: the gate visibly blocks its bad orders.
  - **Check first:** the signal feed must be able to carry bad prices and sizes. Verify `leaders/signal.py` validation (qty up to 1,000,000, price format) and the signal format v1 in `docs/SIGNALS.md` do not reject them before the gate sees them, or the demo will show nothing being blocked. Decide whether to relax validation for this leader or adjust the bad values to pass validation but breach the follower's rules.
- [ ] Keep everything long-only; label simulated data.
- [ ] Order-routing interface with a paper implementation and a broker-adapter stub.

### Phase 6: Audit and dashboard UI
- [ ] `/dashboard`: P&L, positions, rejects by rule, live stream, charts (install uPlot).
- [ ] `/audit`: log table, rejection breakdown, report view, recon and counterfactual views, anchor status (local by default, opt-in devnet publish with transaction link), export.
- [ ] Leader page: track record and "leader result vs. your protected result" side by side.

### Phase 7: Polish and extras
- [ ] Simulator page (pick a profile, watch the gate catch bad behaviour).
- [ ] Optional: Gemini explanations (structured facts only, cite seq), ElevenLabs alert text, Tiger Data sink.
- [ ] Broker adapter at release time.
- [ ] Mainnet decision once the free-production question is settled.

## Known gaps to document or close

- Tail truncation of the log is undetectable (closed up to the last checkpoint by Phase 3).
- Default macOS `os.fsync` does not survive power loss (`F_FULLFSYNC` is opt-in and about 208x slower per call).
- Gate `ts` is wall-clock, so two live runs never hash identically (deterministic only with the injected clock).
- Simulator price model is a toy; static reference prices drift outside the band on long runs.
- Real network paths (Solana devnet, live Alpaca) have never been exercised by tests.

## Open questions

- When to revisit mainnet, and who pays the fees?
