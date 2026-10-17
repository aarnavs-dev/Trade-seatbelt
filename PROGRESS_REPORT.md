# Progress report, 2026-10-15 to 2026-10-17

Covers everything done since `PROJECT_STATUS_REPORT.md` (snapshot of `bb5c23f`). Plan and decisions: `PLAN.md`. Project rules: `CLAUDE.md`.
Everything below is simulated/paper work; nothing here touches real money, a broker or mainnet.

## Summary

| Area | Result |
|---|---|
| Phase 1: foundations | Done (4 steps, 4 commits). |
| Phase 2: log format (rules record) | Done and verified on a clean clone. |
| Side work, Part 2: signal source and provenance on `/follow` | Done. |
| Side work, Part 1: starter portfolio | **On hold** by decision, after a measurement did not support its premise. |
| Tests at HEAD | **407 Python passed, 1 skipped** (Linux-only), **14 web unit tests passed**, Playwright page checks all pass. |
| Commits | 13 on `main` (12 plus this report), pushed together. |

## Phase 1: foundations

1. **Clean-clone verification** of `bb5c23f`: `make setup`, `demo`, `demo-recon`, `test`, `web-test`, `web-build`, `bench` (20k x1, all runs valid), `shots.mjs` and `size.mjs` pass. The one breakage was `web/e2e/follow.mjs`, a test for the removed `/app/follow` page; deleted.
2. **Gitignore and landing edits.** `runs/`, `out/`, `profile/`, `.keys/` etc. are ignored and untracked. The pending landing edits (layout and animation only, copy unchanged) were checked with a build and the page checks, then committed.
3. **Doc fixes.** `DISCLOSURE.md` no longer claims no external service is used (it names Alpaca, yfinance, Solana devnet). `README.md` says `/dashboard` and `/audit` are still placeholders, which is true. The `pace.py` comment now explains the wait as readability only.
4. **`CLAUDE.md` rewritten** for the copy-trading product, from the plan and what step 1 found.

## Phase 2: the rules record (log v2)

**Problem.** The auditor needs the same `rules.yaml` as the gate, and nothing in the log said which rules a gate ran under, so a changed rules file could silently change audited P&L.

**Design (agreed).** A control record inside the hash chain, `{type: "RULES", seq, ts, log_version: 2, rules_hash, prev_hash, hash}`. The hash is SHA-256 of the canonical JSON of the *parsed* rules. The gate loads rules once per process (no live reload), so it appends one record at every start whose rules hash differs from the last recorded one, durably before accepting an order. Order events are byte-identical to v1, so old logs verify unchanged.

**What changed**
- `core`: `RulesRecord`, `CONTROL_TYPES` (the extension point for Phase 3 checkpoints), `rules_hash()`, `Book.apply_record`, `scan_records()` (orders and records) alongside `scan()` (orders only; the whole chain is still verified).
- `gate`: writes the record at start; refuses to start if it cannot make it durable; `replayed_events` counts orders; `Gate(record_rules=False)` writes a v1-style log.
- `auditor`: `RULES_MISMATCH` finding, a `rules_record` report section (MATCH / MISMATCH / PARTIAL / NOT_RECORDED) and a "Rules:" line before the P&L. A v1 log is reported, not a finding, so exit codes and benchmark validity are unchanged. Records are never counted as orders.
- Readers fixed: the SSE tail sends records as a `control` event; the tamper demo never edits the record; `copier/rows.py` skips records (untouched, `/follow` would have shown nothing).
- Demo gained a step that forges the rules record and shows the break at seq 1.

**Tests.** New `tests/test_rules_record.py` (30). Every tamper test in `test_chain.py` now runs on both a v1 and a v2 log. The committed v1 fixture is pinned to the state hash computed by the pre-change code. Invariant 21 added. Fourteen existing tests had seq or timestamp expectations that shifted and were updated rather than loosened.

**Found on the way:** bench runs reused a stale `bench/runs/<id>/gate.log`, which made a second run's profile cases invalid (reproduced on a clean clone, fixed). Two test fragilities fixed (a hard-coded invariant count; a bench-endpoint test that depended on glob order).

**Performance.** The order path is unchanged; the cost is one extra fsync per gate start. A quick re-check was valid on every run, but macOS Low Power Mode was on and the untouched pre-change code measured the same, so it supports **no** performance claim. `docs/PERFORMANCE.md` says its figures predate the record. Re-run `make bench` on AC power before quoting numbers.

## Side work: provenance on `/follow` (approved outside the plan)

**Measured first.** Real yfinance replay of 2026-10-15, scale 1/1, leaders orb / momentum / meanrev (8 / 22 / 6 signals):

| Limits | Skipped before the gate | Blocked by rules | Copied |
|---|---|---|---|
| repo defaults (position 100) | 0 | 0 | all |
| Cautious (position 25, band 2%) | 1 per leader | 1 to 2 | most |
| position 5 | 4 / 10 / 3 | 4 / 12 / 3 | **0** |

A skipped SELL follows a blocked BUY: the leaders trade 10 shares, so a position limit below 10 blocks every BUY. The "follower holds nothing" premise was not supported, so **Part 1 (starter portfolio) was put on hold**. One session, one day of data; not a general result.

**Part 2, built**
- A **Signal source** panel: leader and strategy; the source actually used (live Solana devnet, a saved copy, or a local feed), any fallback with its reason; publisher key; first devnet transaction with an Explorer link; market data label; what Solana does and does not do.
- A **per-row badge**: leader, signal number, source tag, and a transaction link only when the row has one. A row without a transaction is never labelled chain, and a missing or unknown source reads as local. A local feed shows "Not from Solana".
- Counts renamed **"skipped before the gate"** and **"blocked by your rules"**, with the reasons shown.
- Shared Solana wording in `web/src/lib/copy.ts` (used by the landing page too). Invariant 22 added.

## Verification at HEAD

- `make test`: 407 passed, 1 skipped. `npm test`: 14 passed. `npm run build`: passes.
- Playwright `shots.mjs`: all pass (overflow, contrast, no console errors), including new checks of the source panel, warning, badges and counts on a real local run.
- Phase 2 was re-verified on a clean clone of `dba7cca` (398 passed, 1 skipped at that commit). The side-work commits were verified in the working tree, not a fresh clone.

## Not done, and known gaps

- **Part 1 starter portfolio:** on hold. Open question: a leader SELL would sell starter shares the follower never copied in, and starter shares would use position headroom. A smaller alternative for the real cause (clip a copied BUY to the remaining headroom) is not approved or built.
- **Phases 3 to 7 not started:** checkpoints and the local anchor interface, network anchors (devnet, OpenTimestamps, mainnet), more leaders and a broker interface, the real `/dashboard` and `/audit`, polish and optional integrations.
- `/dashboard` and `/audit` are still placeholders.
- Solana devnet and live Alpaca have never been exercised against the real network by the tests (fakes only). The source panel shows what the run's manifest says; it does not re-verify signatures (the copier's allowlist does).
- What goes on chain today is the leader's signal, not a hash.
- Tail truncation of the log is still undetectable until Phase 3.
- The committed benchmark predates the rules record and was not re-run.
- My browser checks overwrite the gitignored `profile/rules.json` (currently a position-5 profile), and one of them stopped a dev server on port 8000 that I had not started.

## Where to look

`PLAN.md` (phases and decisions) · `CLAUDE.md` (current state) · `docs/INVARIANTS.md` (rows 21, 22) · `docs/LIMITATIONS.md` (rules record, provenance, skip measurement) · `tests/test_rules_record.py` · `tests/test_follow_api.py`.
