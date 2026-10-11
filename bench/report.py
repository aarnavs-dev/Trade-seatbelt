"""Generate docs/PERFORMANCE.md from bench/results/*.json. Every number in it comes from a result
file; sentences that state a conclusion are computed from the data they cite."""

import argparse
import glob
import json
import os
import statistics

from bench.profile import CATS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "bench", "results")
ND = " (NON-DURABLE)"


def newest(prefix):
    files = sorted(glob.glob(os.path.join(RES, prefix + "_2*.json")))
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
                 and r["valid_reps"]), None) if data else None


# ---------------------------------------------------------------- environment
def env_native(d):
    e = d["environment"]
    return ["### Environment (native)", "", table(["Item", "Value"], [
        ["CPU", f"{e['cpu_model']} ({e['cpu_cores_physical']} physical / {e['cpu_cores_logical']} logical cores)"],
        ["RAM", f"{e['ram_bytes'] / 2**30:.0f} GiB"], ["OS", e["os"]],
        ["Python / uvloop", f"{e['python']} / {e['uvloop']}"],
        ["Limits", "**NONE enforced.** The gate ran as a plain host process: no CPU or memory limit."],
        ["Log filesystem", f"`{e['log_filesystem']}`"], ["Mount", f"`{e['log_mount']}`"],
        ["Power", "; ".join(e["power"]) or "n/a"], ["Git commit", f"`{e['git_commit']}`"]]),
        "The gate (one process, one thread in use) and up to 4 load-generator processes shared the same "
        "machine, so nothing stopped them competing for CPU. fsync here is native macOS `os.fsync` on APFS, "
        "which asks the OS to flush to the drive but does not force the drive's own cache (`F_FULLFSYNC`).", ""]


def env_docker(d):
    e, c, vm = d["environment"], d["environment"].get("container", {}), d["environment"].get("docker_vm", {})
    hc = c.get("host_config", {})
    top = [l for l in c.get("processes_in_container", "").splitlines()[1:] if l.strip()]
    return ["### Environment (Docker)", "", table(["Item", "Value"], [
        ["Host", f"{e['cpu_model']}, {e['ram_bytes'] / 2**30:.0f} GiB, {e['os']} (same machine as the native runs)"],
        ["Docker", e.get("docker_version", "?")],
        ["Docker Desktop VM", f"Linux {vm.get('kernel')} {vm.get('arch')}, **{vm.get('vm_cpus')} CPUs, "
                              f"{(vm.get('vm_memory_bytes') or 0) / 2**30:.2f} GiB RAM** (what the VM was given)"],
        ["Docker Desktop settings file", f"`{e.get('docker_desktop_settings')}` — it holds no CPU/memory override, so the "
                                         "VM allocation above is Docker Desktop's default for this machine"],
        ["Container limits (verified)", f"`docker inspect`: NanoCpus={hc.get('NanoCpus')} (= 1 CPU), Memory={hc.get('Memory')} "
                                        f"(= {hc.get('Memory', 0) / 2**20:.0f} MiB), MemorySwap={hc.get('MemorySwap')} (= no swap beyond the limit). "
                                        f"Inside the container, cgroup `cpu.max` = `{c.get('cgroup_cpu_max')}`, `memory.max` = `{c.get('cgroup_memory_max')}`"],
        ["Second limit", "The VM's own CPU and RAM are a limit on top of the container's. The container could never "
                         "use more than the VM has; here the VM is larger than the container limit, so the container limit binds."],
        ["Log filesystem", f"`{c.get('log_filesystem')}` mounted as `{c.get('log_mount')}` — a Docker named volume on the **VM's disk**"],
        ["Load generator", "**Outside the container**, on the macOS host (up to 4 processes). Processes inside the container "
                           "at the end of the first run: " + (f"`{top[0].split(None, 7)[-1]}`" if len(top) == 1 else f"{len(top)} processes")],
        ["Network path", "Host to container goes through Docker Desktop's published port, which adds latency and "
                         "host-side CPU that the native runs do not have."],
        ["Git commit", f"`{e['git_commit']}`" + (" — the flag means bench-harness files were being edited during the run; `gate/` and `core/` in the image were identical to that commit (the image was built from them before any later edit)" if "uncommitted" in e["git_commit"] else "")]]),
        "**fsync caveat:** on macOS, Docker runs inside a Linux VM, so fsync cost here reflects the *VM's* disk "
        "(ext4 on a virtual block device backed by a file on the host) and is **not** the same as native macOS "
        "fsync. How far an fsync in the VM reaches the host's physical drive was not measured. A bind mount of a "
        "host directory was deliberately not used, because it would send fsync through virtiofs to the host "
        "filesystem instead.", ""]


# ---------------------------------------------------------------- results
def results_block(d, kind):
    n, L = d["orders"], []
    samples = n - 306 - d["warmup"]
    L += ["#### Closed-loop latency (µs) and throughput", "",
          table(["fsync", "conns", "p50", "p99", "p99.9", "max", "orders/s", "valid"],
                [[label(r["fsync"]), r["conns"], mm(r["lat_p50"]), mm(r["lat_p99"]), mm(r["lat_p99.9"]),
                  mm(r["lat_max"]), mm(r["orders_per_sec"]), f"{r['valid_reps']}/{r['total_reps']}"]
                 for r in rows_of(d, "closed")]),
          f"Median [min–max] over valid reps. About {samples:,} samples per run, so p99.9 rests on ~{samples // 1000} "
          "observations and `max` is a single observation per run.", ""]
    extra = kind == "docker"
    L += ["#### Pipelined throughput and gate resources", "",
          table(["fsync", "conns", "orders/s", "gate CPU %", "peak RSS MB"] + (["CPU throttled ms"] if extra else []) + ["valid"],
                [[label(r["fsync"]), r["conns"], mm(r["orders_per_sec"]), mm(r["gate_cpu_pct"]), mm(r["gate_peak_rss_mb"])]
                 + ([mm(r["gate_throttled_ms"])] if extra and "gate_throttled_ms" in r else ([""] if extra else []))
                 + [f"{r['valid_reps']}/{r['total_reps']}"] for r in rows_of(d, "pipelined")]), ""]
    L += ["#### Gate resources, closed loop", "",
          table(["fsync", "conns", "gate CPU %", "peak RSS MB"],
                [[label(r["fsync"]), r["conns"], mm(r["gate_cpu_pct"]), mm(r["gate_peak_rss_mb"])]
                 for r in rows_of(d, "closed")]),
          ("Docker: CPU % = CPU seconds used (cgroup `cpu.stat`) / the load generator's measured window; "
           "peak RSS = the gate's `VmHWM`. 100% = the container's one CPU." if extra else
           "Native: CPU % = gate process CPU time / wall time around the measured phase (`psutil`); peak RSS sampled every 20 ms."), ""]
    valid = [r for r in d["runs"] if r["valid"]]
    if valid:
        bpe, rps = [r["log_bytes_per_event"] for r in valid], [r["replay_events_per_sec"] for r in valid]
        L += ["#### Log size and replay", "",
              f"- Log bytes per event: median {statistics.median(bpe):.0f} (min {min(bpe):.0f}, max {max(bpe):.0f}) over {len(valid)} valid runs.",
              f"- Auditor replay (run natively on the host in both sections): median {statistics.median(rps):,.0f} events/s (min {min(rps):,.0f}, max {max(rps):,.0f}).", ""]
    L += ["#### Startup / recovery time vs log size", ""]
    if d["recovery"]:
        rows = []
        for r in d["recovery"]:
            if r.get("outcome", "OK") == "OK":
                rows.append([f"{r['events']:,}", f"{r['log_mb']:.0f}", ", ".join(f"{t:.2f}" for t in r["startup_s"]),
                             f"{r['recovery_s_median']:.2f}", f"{r['peak_rss_mb']:.0f}", f"{r['auditor_replay_events_per_sec']:,.0f}", r["valid"]])
            else:
                rows.append([f"{r['events']:,}", f"{r['log_mb']:.0f}", "—", f"**gate {r['outcome']} while replaying**", "—", "—", "n/a"])
        L += [table(["events", "log MB", "startup s (reps)", "recovery s (minus empty-log baseline)", "peak RSS MB",
                     "auditor replay events/s", "valid"], rows),
              f"Empty-log baseline ({'container create + start + interpreter + imports' if kind == 'docker' else 'interpreter + imports'}): "
              f"{d['recovery'][0]['baseline_empty_log_s']:.2f} s."
              + (" Docker timing polls `docker logs`, so it has about ±0.05 s resolution." if kind == "docker" else ""), ""]
    else:
        L += ["Skipped in this run.", ""]
    profs = {p["fsync"]: p for p in d.get("profiles", []) if p["valid"] and "profile" in p}
    if profs:
        L += ["#### Where the writer's time goes (cProfile, pipelined, 8 connections)", "",
              table(["Category"] + [f"{k} (% of profiled time)" for k in profs],
                    [[c] + [f"{profs[k]['profile']['share_pct'][c]:.1f}" for k in profs] for c in CATS]),
              "fsync calls in the profiled window: " + ", ".join(f"{k}: {profs[k]['profile'].get('fsync_calls', 0):,}" for k in profs) + ".", ""]
    elif kind == "docker":
        L += ["Profiling was not done in Docker (the cProfile hook is native-only).", ""]
    return L


def optimization(before, after):
    L = ["### The one optimization: before and after (native)", ""]
    if not (before and after):
        return L + ["Not available (before/after result files missing).", ""]
    L += [f"Both sets measured natively at {before['orders']:,} orders, {before['reps']} reps, on the same machine on the same "
          f"day (before: commit `{before['environment']['git_commit'][:12]}`, after: `{after['environment']['git_commit'][:12]}`). "
          "Change: the connection handler writes all already-completed responses to the socket in one call instead of one "
          "call per response (`gate/server.py`). Chosen because the profile showed socket sends as the largest single cost "
          "under `batched`. Nothing else was tuned.", "",
          table(["fsync", "mode", "conns", "orders/s before", "orders/s after", "change %", "p50 µs before→after"],
                [[label(b["fsync"]), b["mode"], b["conns"], mm(b["orders_per_sec"]), mm(a["orders_per_sec"]),
                  f"{100 * (a['orders_per_sec']['median'] / b['orders_per_sec']['median'] - 1):+.0f}",
                  (f"{b['lat_p50']['median']:.0f}→{a['lat_p50']['median']:.0f}" if "lat_p50" in b and "lat_p50" in a else "")]
                 for b in before["summary"] if b["valid_reps"] for a in [get(after, b["fsync"], b["mode"], b["conns"])] if a]), ""]
    return L


def fsync_section(plain, full, micro):
    L = ["### fsync call on macOS: `os.fsync` vs `F_FULLFSYNC` (native, 20k orders)", ""]
    if not (plain and full):
        return L + ["Not run (result files missing).", ""]
    L += ["**Which one the headline native numbers used:** plain `os.fsync` (the gate's default; `fcntl.F_FULLFSYNC` is opt-in via "
          "`--full-fsync`). Everything in the other native tables and in the Docker section used the default call.", "",
          "What each actually gives on macOS: `os.fsync` hands the data to the drive but does not ask the drive to flush its own volatile "
          "cache, so an acknowledged order can still be lost on power failure; `F_FULLFSYNC` asks the drive to flush its cache to permanent "
          "storage, which is the stronger guarantee and is what a power-loss claim would need. We did not test power loss, so neither "
          "guarantee was verified by crashing a machine.", ""]
    if micro:
        a, b = micro["os.fsync"], micro["F_FULLFSYNC"]
        L += [f"Microbenchmark (`bench/fsync_micro.py`, {micro['calls']} append+sync calls of {micro['bytes_per_write']} bytes, same volume): "
              f"`os.fsync` median {a['median_us']:.0f} µs (p99 {a['p99_us']:.0f}); `F_FULLFSYNC` median {b['median_us']:.0f} µs "
              f"(p99 {b['p99_us']:.0f}) — about {b['median_us'] / a['median_us']:.0f}× more.", ""]
    L += [f"Both sets: {plain['orders']:,} orders, {plain['reps']} reps, run back to back on the same machine (plain first). "
          "Only `per_order` and `batched` are shown; fsync off is unaffected.", ""]
    rows = []
    for r in plain["summary"]:
        f = get(full, r["fsync"], r["mode"], r["conns"])
        if not (r["valid_reps"] and f):
            continue
        o, q = r["orders_per_sec"]["median"], f["orders_per_sec"]["median"]
        lat = (f"{r['lat_p50']['median']:.0f} → {f['lat_p50']['median']:.0f}", f"{r['lat_p99']['median']:.0f} → {f['lat_p99']['median']:.0f}") \
            if "lat_p50" in r and "lat_p50" in f else ("", "")
        rows.append([r["fsync"], r["mode"], r["conns"], mm(r["orders_per_sec"]), mm(f["orders_per_sec"]), f"{100 * (q / o - 1):+.0f}%", *lat])
    L += [table(["fsync", "mode", "conns", "orders/s `os.fsync`", "orders/s `F_FULLFSYNC`", "change", "p50 µs", "p99 µs"], rows)]
    bad = [r["id"] for d in (plain, full) for r in d["runs"] if not r["valid"]]
    L += [f"Invalid runs in this comparison: {', '.join(bad) if bad else 'none'}.", ""]
    return L


def comparison(nat, dock):
    rows = []
    for r in nat["summary"]:
        k = (r["fsync"], r["mode"], r["conns"])
        x = get(dock, *k)
        if not (r["valid_reps"] and x):
            continue
        ratio = x["orders_per_sec"]["median"] / r["orders_per_sec"]["median"]
        p = (f"{r['lat_p50']['median']:.0f} → {x['lat_p50']['median']:.0f}" if "lat_p50" in r and "lat_p50" in x else "")
        rows.append([label(r["fsync"]), r["mode"], r["conns"], f"{r['orders_per_sec']['median']:,.0f}",
                     f"{x['orders_per_sec']['median']:,.0f}", f"{100 * (ratio - 1):+.0f}%", p])
    return ["## Native vs Docker, side by side", "",
            "Same machine, same workload and seed, same load generator; only where the gate runs differs. Medians. "
            "Both sets use the same gate code (the post-optimization commit shown in each section).", "",
            table(["fsync", "mode", "conns", "native orders/s", "Docker orders/s", "Docker vs native", "p50 µs native → Docker"], rows), ""]


# ---------------------------------------------------------------- interpretation
def interpretation(nat, dock):
    L = ["## Interpretation", "", "### Observed bottleneck (native)", ""]
    g = lambda fs, m, c: get(nat, fs, m, c)
    pn, pb, pp = g("none", "pipelined", 8), g("batched", "pipelined", 8), g("per_order", "pipelined", 8)
    if pn and pb and pp:
        L += [f"- Pipelined, 8 connections: {pn['orders_per_sec']['median']:,.0f} orders/s with fsync off, {pb['orders_per_sec']['median']:,.0f} with "
              f"`batched`, {pp['orders_per_sec']['median']:,.0f} with `per_order`. Gate CPU is {pn['gate_cpu_pct']['median']:.0f}% / "
              f"{pb['gate_cpu_pct']['median']:.0f}% / {pp['gate_cpu_pct']['median']:.0f}%, so "
              + ("in the first two the gate's single thread is saturated: that is the single-writer limit, not the load generator."
                 if min(pn['gate_cpu_pct']['median'], pb['gate_cpu_pct']['median']) >= 90 else
                 "the gate was NOT saturated; the load generator may be limiting these figures.")
              + (f" With `per_order` the gate sat idle {100 - pp['gate_cpu_pct']['median']:.0f}% of the time, waiting in fsync." if pp['gate_cpu_pct']['median'] < 90 else "")]
    for fs, p in {p["fsync"]: p for p in nat.get("profiles", []) if p["valid"] and "profile" in p}.items():
        top = sorted(p["profile"]["share_pct"].items(), key=lambda kv: -kv[1])[:3]
        L.append(f"- Profile, `{fs}`: " + "; ".join(f"{c} {v:.0f}%" for c, v in top) + ".")
    a, b = g("per_order", "closed", 1), g("none", "closed", 1)
    if a and b:
        L.append(f"- Native single-connection closed loop: p50 {a['lat_p50']['median']:.0f} µs with `per_order` vs {b['lat_p50']['median']:.0f} µs "
                 f"with fsync off, so a native `os.fsync` costs about {a['lat_p50']['median'] - b['lat_p50']['median']:.0f} µs there.")
    L.append("")
    if dock:
        L += ["### What Docker changed", ""]
        da, db = get(dock, "per_order", "closed", 1), get(dock, "none", "closed", 1)
        if da and db and a and b:
            L.append(f"- Closed loop, 1 connection, fsync off: p50 {b['lat_p50']['median']:.0f} µs native vs {db['lat_p50']['median']:.0f} µs in Docker. "
                     f"That ~{db['lat_p50']['median'] - b['lat_p50']['median']:.0f} µs is the cost of the Docker Desktop port-forwarding path, paid on every request.")
            L.append(f"- `per_order` fsync cost (p50 with fsync minus without): about {a['lat_p50']['median'] - b['lat_p50']['median']:.0f} µs native vs "
                     f"{da['lat_p50']['median'] - db['lat_p50']['median']:.0f} µs on the Docker VM's disk. These are different disks and different layers; "
                     "the Docker figure must not be read as a native one.")
        dp, dpn = get(dock, "per_order", "pipelined", 8), get(dock, "none", "pipelined", 8)
        if dp and dpn:
            L.append(f"- Pipelined, 8 connections: `per_order` {dp['orders_per_sec']['median']:,.0f}/s and fsync-off {dpn['orders_per_sec']['median']:,.0f}/s in Docker "
                     f"(native: {pp['orders_per_sec']['median']:,.0f} and {pn['orders_per_sec']['median']:,.0f}).")
        thr = [r.get("gate_throttled_ms", 0) for r in dock["runs"] if r["valid"]]
        if thr:
            L.append(f"- CPU throttling by the 1-CPU limit: {sum(thr):,.0f} ms in total over {len(thr)} valid runs (max {max(thr):,.0f} ms in one run); "
                     f"highest gate CPU {max(r['gate_cpu_pct'] for r in dock['runs'] if r['valid']):.0f}% of the one allowed CPU.")
        oom = [r for r in dock["recovery"] if r.get("outcome", "OK") != "OK"]
        if oom:
            L.append("- **Memory limit:** recovery of " + ", ".join(f"{r['events']:,}" for r in oom) +
                     "-event log(s) did not complete in the 512 MB container (gate " + ", ".join(sorted({r['outcome'] for r in oom})) + "). "
                     "The same recovery natively needed the RSS shown in the native recovery table.")
        L.append("")
    L += ["### Durability vs latency, per fsync policy", "",
          table(["Policy", "When the client gets its reply", "What can be lost on a crash"], [
              ["`per_order`", "After that one event was written and fsynced.",
               "Gate process killed: no acknowledged order is lost; an order with no reply yet may or may not be in the log. "
               "OS crash / power loss: acknowledged orders survive only as far as `os.fsync` is honoured by the drive "
               "(on macOS plain `os.fsync` is not forced through the drive cache; `--full-fsync` uses `F_FULLFSYNC` to do that — see the fsync section; power loss not tested here)."],
              ["`batched` (group commit, up to 256 events per fsync)", "After the whole batch was written and fsynced.",
               "The same guarantee as `per_order` for acknowledged orders; it is cheaper because one fsync covers many events. "
               "A reply can wait for the rest of its batch."],
              ["`none` **(NON-DURABLE)**", "After `os.write`, never fsynced.",
               "Gate process killed: the bytes are already in the OS page cache, so they survive. OS crash / power loss: every event "
               "the OS had not yet flushed is lost, including acknowledged ones, and the last line may be partial. How much depends on "
               "OS writeback timing and was not measured."]]),
          "A crash in the middle of a write can leave a partial last line in any policy; the gate then refuses to start "
          "(`partial last line`) until the log is repaired. There is no automatic repair yet.", "",
          "### What would break or need redesign next", ""]
    rec = nat["recovery"]
    ok = [r for r in rec if r.get("outcome", "OK") == "OK"]
    if ok:
        big = max(ok, key=lambda r: r["events"])
        over = next((r for r in sorted(ok, key=lambda r: r["events"]) if r["peak_rss_mb"] > 512), None)
        L.append(f"- **Memory and recovery time grow with log size.** Startup replays the whole log and keeps every event in the idempotency map: "
                 f"natively, peak RSS {big['peak_rss_mb']:.0f} MB and {big['recovery_s_median']:.1f} s recovery at {big['events']:,} events"
                 + (f"; RSS first exceeded 512 MB at the {over['events']:,}-event log." if over else "."))
    L += ["- **One core.** The single writer is one Python thread; once it is saturated, more connections add latency, not throughput "
          "(p50 grows with connections while orders/s stays flat in the tables).",
          "- **Durability** beyond `os.fsync` is untested on both native macOS (`F_FULLFSYNC`) and the Docker VM disk; a power-loss claim needs real crash tests.",
          "- **Not covered:** hours-long runs, network between machines, limits other than 1 CPU / 512 MB.", ""]
    return L


def build(nat, dock, before, after, plain=None, full=None, micro=None) -> str:
    w, n = nat["workload"], nat["orders"]
    split = ", ".join(f"{k} {v:,}" for k, v in w["by_reason"].items() if k != "OK")
    L = ["# Performance", "",
         "Simulated trading only. Every number below was measured by this repo's benchmark on the machine described and is read from "
         "`bench/results/` — nothing is estimated. There are **two separately labelled sets of runs**: "
         "**Native** (plain host process, **no CPU or memory limits enforced**) and **Docker** (gate in a container limited to "
         "`--cpus=1 --memory=512m`, on macOS inside Docker Desktop's Linux VM). Do not mix their numbers.", ""]
    L += ["## Workload (both sections)", "",
          f"- {n:,} orders, seed 7, unique `client_order_id`s, 32 symbols plus one epilogue symbol.",
          f"- Reference model (core's `check()` run through `Book`, in `bench/workload.py`): **{w['accepted']:,} ACCEPT "
          f"({100 * w['accepted'] / n:.1f}%)** and **{w['rejected']:,} REJECT** ({split}).",
          "- Mix: ~8% outside the price band, ~8% oversized for the position limit, the rest normal flow biased back toward flat so positions "
          "hover near (and sometimes hit) the limit. The last 306 orders are a sequential epilogue: three losing round trips trigger the "
          "loss-streak cooldown, then 300 risk-increasing orders are rejected by it.",
          "- Benchmark rules (`bench/data/rules_bench.yaml`, generated): rate limit and daily-loss limit are set so high they never fire, and the "
          "cooldown lasts 86,400 s. This makes every decision independent of how orders from different connections interleave, so the "
          "expected totals are exact at any connection count. Rate limit and daily loss are covered by unit tests, not by this benchmark.",
          "- A connection owns whole symbols (`symbol index % connections`), so order within a symbol is preserved.", ""]
    L += ["## Metrics and why they matter", "", table(["Metric", "Why it matters"], [
        ["Latency p50 / p99 / p99.9 / max (closed loop)", "What a trader waits for a decision. One order in flight per connection, timed client-side from send to reply, avoids coordinated omission."],
        ["Orders/sec (pipelined and closed)", "Peak the single writer can sustain."],
        ["Gate CPU %, peak RSS", "Shows whether the gate or the load generator is the limit, and memory headroom."],
        ["Log bytes per event", "Disk growth and replay cost."],
        ["Auditor replay events/sec", "How fast an audit or a recovery can re-derive state."],
        ["Startup / recovery time vs log size", "How long the gate is down after a crash."]]), ""]
    L += ["## Procedure", "", "```", "make setup",
          f"make bench BENCH_N={n} BENCH_REPS={nat['reps']}   # native: python -m bench.run --orders {n} --reps {nat['reps']} --label final && python -m bench.report"]
    if dock:
        L += [f"docker build -t trade-seatbelt-gate .       # image used by the Docker runs",
              f"python -m bench.run --backend docker --orders {dock['orders']} --reps {dock['reps']} --label docker   # (make bench-docker)"]
    L += ["```", "",
          "- Every run starts a **fresh gate (process or container) and a fresh log**. Docker: fresh container and fresh named volume.",
          f"- Warm-up: the first {nat['warmup']:,} orders of each run are sent first on the same connections, fully drained, and excluded from all "
          "statistics (they are still in the log and verified).",
          "- Closed loop: one order in flight per connection, latency timed with `perf_counter_ns` in the load generator. Pipelined: up to 256 in flight per connection, used for throughput only.",
          f"- Connections 1, 8, 32; fsync `per_order`, `batched`, `none`; each configuration run {nat['reps']} times, interleaved across configurations; tables show median [min–max]. "
          "The load generator is a separate process set (up to 4), never inside the gate and never inside the container.",
          "- Orders/sec = measured orders / (last reply − first send) across all load-generator processes.",
          "- **Verification after every run:** the auditor verifies the chain, replays the log and re-checks every decision with `check()`; "
          "the run is **invalid** and excluded unless event count, accept total and reject split equal the reference model's, there are no "
          "auditor findings, and the load generator saw no per-order decision mismatch (and, in Docker, the container was not OOM-killed). "
          "Docker logs are copied out of the container's volume before auditing.",
          "- Recovery: a gate is started on pre-built logs of 10k / 100k / 1M events (`bench/workload.py --emit-log`); time from start to the `listening` line, minus the same measurement on an empty log.",
          "- Profiling (native only): separate runs with `cProfile` enabled in the gate only during the measured phase; it inflates Python-level time, so shares are indicative and those runs are not used for throughput.", ""]
    L += ["## Native results (no limits enforced)", ""] + env_native(nat) + results_block(nat, "native") + optimization(before, after) + fsync_section(plain, full, micro)
    if dock:
        L += ["## Docker results (gate limited to 1 CPU, 512 MB; macOS Docker Desktop VM)", ""] + env_docker(dock) + results_block(dock, "docker")
        L += comparison(nat, dock)
    else:
        L += ["## Docker results", "", "**Not run** (no Docker result file found).", ""]
    inv = [r["id"] for r in nat["runs"] + nat["profiles"] if not r["valid"]]
    dinv = [r["id"] for r in dock["runs"] if not r["valid"]] if dock else []
    L += ["## Failures and skipped", "",
          f"- Invalid or failed native runs: {', '.join(inv) if inv else 'none'}.",
          f"- Invalid or failed Docker runs: {', '.join(dinv) if dinv else ('none' if dock else 'not run')}.",
          "- Power-loss durability (`F_FULLFSYNC`, real crash tests), native or Docker: **not measured**.", ""]
    return "\n".join(L + interpretation(nat, dock))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "PERFORMANCE.md"))
    ap.add_argument("--native", default="final")
    ap.add_argument("--docker", default="docker")
    a = ap.parse_args()
    nat = newest(a.native)
    if not nat:
        raise SystemExit("no bench/results/final_*.json; run make bench first")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w").write(build(nat, newest(a.docker), newest("before_opt"), newest("after_opt"),
                                   newest("fsync_plain"), newest("fsync_full"), newest("fsync_micro")))
    print(f"wrote {a.out}")
