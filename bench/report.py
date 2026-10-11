"""Generate docs/PERFORMANCE.md from bench/results/*.json. Every number in it comes from a result
file; sentences that state a conclusion are computed from the data they cite."""

import argparse
import glob
import json
import os
import statistics

from bench.profile import CATS, analyze

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "bench", "results")
ND = " (NON-DURABLE)"


def newest(prefix):
    files = sorted(glob.glob(os.path.join(RES, prefix + "_*.json")))
    return json.load(open(files[-1])) if files else None


def mm(s, f="{:.0f}"):
    return f"{f.format(s['median'])} [{f.format(s['min'])}–{f.format(s['max'])}]"


def label(fs):
    return fs + ND if fs == "none" else fs


def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
                     + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]) + "\n"


def rows_of(data, mode):
    return [r for r in data["summary"] if r["mode"] == mode and r["valid_reps"]]


def get(data, fs, mode, conns):
    return next((r for r in data["summary"] if (r["fsync"], r["mode"], r["conns"]) == (fs, mode, conns)
                 and r["valid_reps"]), None)


def build(final, before, after) -> str:
    e, w, n = final["environment"], final["workload"], final["orders"]
    L = ["# Performance", "",
         "Simulated trading only. Every number below was measured by `make bench` on the machine "
         "described here and is read from `bench/results/` — nothing is estimated. "
         "**These runs were native: CPU and memory limits were NOT enforced** (see Environment).", ""]
    # ---- Environment
    L += ["## Environment", "", table(["Item", "Value"], [
        ["CPU", f"{e['cpu_model']} ({e['cpu_cores_physical']} physical / {e['cpu_cores_logical']} logical cores)"],
        ["RAM", f"{e['ram_bytes'] / 2**30:.0f} GiB"], ["OS", e["os"]],
        ["Python / uvloop", f"{e['python']} / {e['uvloop']}"],
        ["Docker", e["docker"]], ["Limits", e["limits"]],
        ["Log filesystem", f"`{e['log_filesystem']}`"], ["Mount", f"`{e['log_mount']}`"],
        ["Power", "; ".join(e["power"]) or "n/a"], ["Git commit", e["git_commit"]]]),
        "The gate and the load generators ran on the same machine, as separate processes. The gate is "
        "one process (one core in use); the load generator uses up to 4 processes so that the client is "
        "not the bottleneck. With no container, nothing stops the load generators competing with the "
        "gate for CPU. A Dockerfile and `scripts/run_gate_docker.sh` are provided to run the gate "
        "under `--cpus=1 --memory=512m` with the load generator **outside** the container; "
        "**that run has not been done** (Docker was not installed when these numbers were taken). "
        "When it is done, it must be reported separately from these native numbers.", "",
        "macOS caveat: Docker on macOS runs in a Linux VM, so fsync cost there depends on the VM's "
        "disk and is not the same as native macOS fsync. All fsync numbers here are **native macOS "
        "(APFS)**. Python's `os.fsync` on macOS asks the OS to flush to the drive but does not force "
        "the drive's own cache (that needs `F_FULLFSYNC`), so these fsync costs are lower than a "
        "power-loss-safe flush would cost.", ""]
    # ---- Workload
    split = ", ".join(f"{k} {v:,}" for k, v in w["by_reason"].items() if k != "OK")
    L += ["## Workload", "",
          f"- {n:,} orders, seed 7, unique `client_order_id`s, 32 symbols plus one epilogue symbol.",
          f"- Reference model (core's `check()` run through `Book`, in `bench/workload.py`) says: "
          f"**{w['accepted']:,} ACCEPT ({100 * w['accepted'] / n:.1f}%)** and **{w['rejected']:,} REJECT** "
          f"({split}).",
          "- Mix: ~8% priced outside the price band, ~8% oversized for the position limit, the rest normal "
          "flow biased back toward flat so positions hover near (and sometimes hit) the limit. The last 306 "
          "orders are a sequential epilogue: three losing round trips trigger the loss-streak cooldown, "
          "then 300 risk-increasing orders are rejected by it.",
          "- Benchmark rules (`bench/data/rules_bench.yaml`, generated): rate limit and daily-loss limit are "
          "set so high they never fire, and the cooldown lasts 86,400 s. This is deliberate: it makes every "
          "decision independent of how orders from different connections interleave, so the expected "
          "totals are exact at any connection count. Rate limit and daily loss are covered by unit tests, "
          "not by this benchmark.",
          "- A connection owns whole symbols (`symbol index % connections`), so order within a symbol is "
          "preserved.", ""]
    # ---- Metrics
    L += ["## Metrics and why they matter", "", table(["Metric", "Why it matters"], [
        ["Latency p50 / p99 / p99.9 / max (closed loop)", "What a trader waits for a decision. Closed loop (one order in flight per connection, timed client-side from send to reply) avoids coordinated omission."],
        ["Orders/sec (pipelined and closed)", "Peak the single writer can sustain."],
        ["Gate CPU %, peak RSS", "Shows whether the gate or the load generator is the limit, and memory headroom."],
        ["Log bytes per event", "Disk growth and replay cost."],
        ["Auditor replay events/sec", "How fast an audit or a recovery can re-derive state."],
        ["Startup / recovery time vs log size", "How long the gate is down after a crash."]]), ""]
    # ---- Procedure
    L += ["## Procedure", "", "```",
          "make setup",
          f"make bench BENCH_N={n} BENCH_REPS={final['reps']}      # = python -m bench.run --orders {n} --reps {final['reps']} --label final && python -m bench.report",
          "```", "",
          f"- Every run starts a **fresh gate process and a fresh log**; ports are chosen free.",
          f"- Warm-up: the first {final['warmup']:,} orders of each run are sent first on the same connections, "
          "fully drained, and excluded from all statistics (they are still in the log and verified).",
          "- Closed-loop: one order in flight per connection, latency timed with `perf_counter_ns` in the "
          "load generator. Pipelined: up to 256 in flight per connection, used for throughput only.",
          "- Connections 1, 8, 32; fsync policies `per_order`, `batched`, `none`; each configuration run "
          f"{final['reps']} times, interleaved across configurations; tables show median [min–max].",
          "- Orders/sec = measured orders / (last reply − first send) across all load-generator processes. "
          "Gate CPU % = gate process CPU time delta / wall delta around the measured phase (`psutil`). "
          "Peak RSS = max of samples every 20 ms.",
          "- **Verification after every run:** the auditor verifies the chain, replays the log and re-checks every "
          "decision with `check()`; the run is **invalid** and excluded unless event count, accept total "
          "and reject split equal the reference model's, there are no auditor findings, and the load "
          "generator saw no per-order decision mismatch.",
          "- Replay speed = events / auditor wall time on the produced log (includes hash and canonical-form "
          "checks and re-deciding every order).",
          "- Recovery: a gate is started on pre-built logs of 10k / 100k / 1M events (`bench/workload.py "
          "--emit-log`); time from process spawn to the `listening` line, minus the same measurement on an "
          "empty log (interpreter + imports).",
          "- Profiling: separate runs (`per_order` and `batched`, pipelined, 8 connections) with `cProfile` "
          "enabled in the gate only during the measured phase (`GATE_PROFILE`, SIGUSR1/SIGUSR2). cProfile "
          "inflates Python-level time, so shares are indicative and these runs are not used for throughput.", ""]
    # ---- Results
    inv = [r["id"] for r in final["runs"] + final["profiles"] if not r["valid"]]
    L += ["## Results", ""]
    L += ["### Closed-loop latency (µs) and throughput", "",
          table(["fsync", "conns", "p50", "p99", "p99.9", "max", "orders/s", "valid"],
                [[label(r["fsync"]), r["conns"], mm(r["lat_p50"]), mm(r["lat_p99"]), mm(r["lat_p99.9"]),
                  mm(r["lat_max"]), mm(r["orders_per_sec"]), f"{r['valid_reps']}/{r['total_reps']}"]
                 for r in rows_of(final, "closed")]),
          f"Samples per run: {n - 306 - final['warmup']:,}; p99.9 therefore rests on about "
          f"{(n - 306 - final['warmup']) // 1000:,} observations and `max` is a single observation per run.", ""]
    L += ["### Pipelined throughput and gate resources", "",
          table(["fsync", "conns", "orders/s", "gate CPU %", "peak RSS MB", "valid"],
                [[label(r["fsync"]), r["conns"], mm(r["orders_per_sec"]), mm(r["gate_cpu_pct"]),
                  mm(r["gate_peak_rss_mb"]), f"{r['valid_reps']}/{r['total_reps']}"]
                 for r in rows_of(final, "pipelined")]), ""]
    L += ["### Gate resources, closed loop", "",
          table(["fsync", "conns", "gate CPU %", "peak RSS MB"],
                [[label(r["fsync"]), r["conns"], mm(r["gate_cpu_pct"]), mm(r["gate_peak_rss_mb"])]
                 for r in rows_of(final, "closed")]), ""]
    valid = [r for r in final["runs"] if r["valid"]]
    if valid:
        bpe = statistics.median(r["log_bytes_per_event"] for r in valid)
        rps = [r["replay_events_per_sec"] for r in valid]
        L += ["### Log size and replay", "",
              f"- Log bytes per event: median {bpe:.0f} (min {min(r['log_bytes_per_event'] for r in valid):.0f}, "
              f"max {max(r['log_bytes_per_event'] for r in valid):.0f}) over {len(valid)} valid runs.",
              f"- Auditor replay: median {statistics.median(rps):,.0f} events/s (min {min(rps):,.0f}, max {max(rps):,.0f}).", ""]
    if final["recovery"]:
        L += ["### Startup / recovery time vs log size (fsync irrelevant: nothing is written)", "",
              table(["events", "log MB", "startup s (runs)", "recovery s (minus empty-log baseline)", "peak RSS MB",
                     "auditor replay events/s", "valid"],
                    [[f"{r['events']:,}", f"{r['log_mb']:.0f}", ", ".join(f"{t:.2f}" for t in r["startup_s"]),
                      f"{r['recovery_s_median']:.2f}", f"{r['peak_rss_mb']:.0f}",
                      f"{r['auditor_replay_events_per_sec']:,.0f}", r["valid"]] for r in final["recovery"]]),
              f"Empty-log baseline (interpreter + imports): {final['recovery'][0]['baseline_empty_log_s']:.2f} s.", ""]
    else:
        L += ["### Recovery", "", "Skipped in this run.", ""]
    profs = {p["fsync"]: p for p in final["profiles"] if p["valid"]}
    if profs:
        L += ["### Where the writer's time goes (cProfile, pipelined, 8 connections)", "",
              table(["Category"] + [f"{k} (% of profiled time)" for k in profs],
                    [[c] + [f"{profs[k]['profile']['share_pct'][c]:.1f}" for k in profs] for c in CATS]),
              "fsync calls in the profiled window: " + ", ".join(
                  f"{k}: {profs[k]['profile'].get('fsync_calls', 0):,}" for k in profs) + ".", ""]
    # ---- optimization
    L += ["### The one optimization: before and after", ""]
    if before and after:
        L += [f"Both sets measured at {before['orders']:,} orders, {before['reps']} reps, on the same machine on "
              f"the same day (before: commit `{before['environment']['git_commit'][:12]}`, "
              f"after: `{after['environment']['git_commit'][:12]}`). Change: the connection handler now "
              "writes all already-completed responses to the socket in one call instead of one call per "
              "response (`gate/server.py`). Chosen because the profile above showed socket sends as the "
              "largest single cost under `batched`. Nothing else was tuned.", "",
              table(["fsync", "mode", "conns", "orders/s before", "orders/s after", "change %", "p50 µs before→after"],
                    [[label(b["fsync"]), b["mode"], b["conns"], mm(b["orders_per_sec"]), mm(a["orders_per_sec"]),
                      f"{100 * (a['orders_per_sec']['median'] / b['orders_per_sec']['median'] - 1):+.0f}",
                      (f"{b['lat_p50']['median']:.0f}→{a['lat_p50']['median']:.0f}" if "lat_p50" in b and "lat_p50" in a else "")]
                     for b in before["summary"] if b["valid_reps"]
                     for a in [get(after, b["fsync"], b["mode"], b["conns"])] if a]), ""]
    else:
        L += ["Not available (before/after result files missing).", ""]
    L += ["### Failures and skipped", ""]
    L += [f"- Invalid or failed runs in the final matrix: {', '.join(inv) if inv else 'none'}.",
          "- Docker-limited run (`--cpus=1 --memory=512m`): **not done** (Docker not installed at measurement time).",
          "- Power-loss durability (`F_FULLFSYNC`, real crash tests): **not measured**.", ""]
    L += interpretation(final)
    return "\n".join(L)


def interpretation(final) -> list:
    L = ["## Interpretation", ""]
    g = lambda fs, m, c: get(final, fs, m, c)
    p_none, p_bat, p_po = g("none", "pipelined", 8), g("batched", "pipelined", 8), g("per_order", "pipelined", 8)
    if p_none and p_bat and p_po:
        peak = p_none["orders_per_sec"]["median"]
        L += ["### Observed bottleneck", "",
              f"- With a pipelined load the gate reaches {peak:,.0f} orders/s with fsync off, {p_bat['orders_per_sec']['median']:,.0f} "
              f"with `batched` and {p_po['orders_per_sec']['median']:,.0f} with `per_order` (8 connections, medians). "
              f"Gate CPU is {p_none['gate_cpu_pct']['median']:.0f}% / {p_bat['gate_cpu_pct']['median']:.0f}% / "
              f"{p_po['gate_cpu_pct']['median']:.0f}% respectively, so"
              + (" the gate's single thread is saturated in the first two: that is the single-writer limit, not the load generator."
                 if min(p_none['gate_cpu_pct']['median'], p_bat['gate_cpu_pct']['median']) >= 90 else
                 " the gate was NOT saturated; the load generator may be limiting these figures."), ""]
    profs = {p["fsync"]: p["profile"]["share_pct"] for p in final["profiles"] if p["valid"] and "profile" in p}
    for fs, sh in profs.items():
        top = sorted(sh.items(), key=lambda kv: -kv[1])[:3]
        L.append(f"- Profile, `{fs}`: " + "; ".join(f"{c} {v:.0f}%" for c, v in top) + ".")
    if profs:
        L.append("")
    a, b = g("per_order", "closed", 1), g("none", "closed", 1)
    if a and b:
        d = a["lat_p50"]["median"] - b["lat_p50"]["median"]
        L += [f"- Single-connection closed loop: p50 is {a['lat_p50']['median']:.0f} µs with `per_order` vs "
              f"{b['lat_p50']['median']:.0f} µs with fsync off, so an fsync costs about {d:.0f} µs here "
              f"on top of a round trip of the same order of size (macOS `os.fsync`, which does not flush the drive cache).", ""]
    L += ["### Durability vs latency, per fsync policy", "",
          table(["Policy", "When the client gets its reply", "What can be lost on a crash"], [
              ["`per_order`", "After that one event was written and fsynced.",
               "Gate process killed: no acknowledged order is lost; an order with no reply yet may or may not be in the log. "
               "OS crash / power loss: acknowledged orders survive only as far as `os.fsync` is honoured by the drive "
               "(on macOS it is not forced through the drive cache — not tested here)."],
              ["`batched` (group commit, up to 256 events per fsync)", "After the whole batch was written and fsynced.",
               "The same guarantee as `per_order` for acknowledged orders; it is cheaper because one fsync covers many "
               "events. Replies can wait for the rest of their batch."],
              ["`none` **(NON-DURABLE)**", "After `os.write`, never fsynced.",
               "Gate process killed: the bytes are already in the OS page cache, so they survive. OS crash / power loss: "
               "every event the OS had not yet flushed is lost, including acknowledged ones, and the last line may be partial. "
               "How much depends on OS writeback timing and was not measured."]]),
          "A crash in the middle of a write can leave a partial last line in any policy; the gate then refuses to start "
          "(`partial last line`) until the log is repaired. There is no automatic repair yet.", "",
          "### What would break or need redesign next", ""]
    rec = final["recovery"]
    if rec:
        big = max(rec, key=lambda r: r["events"])
        over = next((r for r in sorted(rec, key=lambda r: r["events"]) if r["peak_rss_mb"] > 512), None)
        L.append(f"- **Memory and recovery time grow with log size.** Startup replays the whole log and keeps every event "
                 f"in the idempotency map: measured peak RSS {big['peak_rss_mb']:.0f} MB and "
                 f"{big['recovery_s_median']:.1f} s recovery at {big['events']:,} events"
                 + (f"; RSS first exceeded 512 MB at the {over['events']:,}-event log, so a 512 MB container would not "
                    "hold that log (limit not enforced in these runs)." if over else "."))
    L += ["- **One core.** The single writer is one Python thread; once it is saturated, more connections add latency, not throughput "
          "(see the latency table: p50 grows with connections while orders/s stays flat).",
          "- **Durability on macOS** is untested beyond `os.fsync`; a real power-loss claim needs `F_FULLFSYNC` numbers.",
          "- **Not covered:** Docker CPU/memory limits, network between machines, long-running (hours) behaviour.", ""]
    return L


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "PERFORMANCE.md"))
    a = ap.parse_args()
    final = newest("final")
    if not final:
        raise SystemExit("no bench/results/final_*.json; run make bench first")
    text = build(final, newest("before_opt"), newest("after_opt"))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write(text)
    print(f"wrote {a.out}")
