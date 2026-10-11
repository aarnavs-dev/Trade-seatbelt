"""Benchmark harness: fresh gate + log per run, separate load-generator processes, verification
after every run. Writes raw results to bench/results/. Usage: python -m bench.run [--orders N]."""

import argparse
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time

import psutil

from auditor.replay import audit
from bench import env, workload
from bench.profile import analyze
from core.rules import load_rules

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
RUNS = os.path.join(ROOT, "bench", "runs")
RESULTS = os.path.join(ROOT, "bench", "results")
FSYNCS = ["per_order", "batched", "none"]
MODES = ["closed", "pipelined"]
CONNS = [1, 8, 32]
WARMUP = 5000


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class GateProc:
    """A fresh gate process with an RSS sampler."""

    def __init__(self, rules, log, fsync, outdir, profile=None):
        self.port, self.outfile = free_port(), os.path.join(outdir, "gate.out")
        env_ = dict(os.environ, **({"GATE_PROFILE": profile} if profile else {}))
        self.t_spawn = time.perf_counter()
        self.p = subprocess.Popen(
            [PY, "-m", "gate", "--rules", rules, "--log", log, "--port", str(self.port),
             "--fsync", fsync], stdout=open(self.outfile, "w"), stderr=subprocess.STDOUT,
            cwd=ROOT, env=env_)
        self.ps, self.peak, self._stop = psutil.Process(self.p.pid), 0, threading.Event()
        self.listening = None
        while self.listening is None:
            if self.p.poll() is not None:
                raise RuntimeError("gate exited at startup: " + open(self.outfile).read())
            if "listening" in open(self.outfile).read():
                self.listening = time.perf_counter() - self.t_spawn
            else:
                time.sleep(0.005)
        threading.Thread(target=self._sample, daemon=True).start()

    def _sample(self):
        while not self._stop.is_set():
            try:
                self.peak = max(self.peak, self.ps.memory_info().rss)
            except psutil.Error:
                return
            time.sleep(0.02)

    def replayed(self) -> int:
        return int(open(self.outfile).read().split("replayed_events=")[1].split()[0])

    def stop(self):
        self._stop.set()
        self.p.send_signal(signal.SIGTERM)
        self.p.wait(timeout=60)


def pct(sorted_ns, q):
    return sorted_ns[max(0, -(-len(sorted_ns) * q // 1000) - 1)] / 1000  # q in permille, -> us


def run_once(cfg, n, rules, wl, expected, rid, profile=None):
    fsync, mode, conns = cfg
    outdir = os.path.join(RUNS, rid)
    os.makedirs(outdir, exist_ok=True)
    log = os.path.join(outdir, "gate.log")
    rec = {"id": rid, "fsync": fsync, "mode": mode, "conns": conns, "n": n, "valid": False, "errors": []}
    gate = GateProc(rules, log, fsync, outdir, profile and os.path.join(outdir, "gate.prof"))
    try:
        procs_n = min(conns, 4)
        procs = []
        for k in range(procs_n):
            mine = ",".join(str(c) for c in range(conns) if c % procs_n == k)
            res = os.path.join(outdir, f"lg{k}.json")
            cmd = [PY, "-m", "bench.loadgen", "--port", str(gate.port), "--workload", wl,
                   "--expected", wl.replace(".jsonl", ".expected.json"), "--result", res,
                   "--mode", mode, "--conns", str(conns), "--mine", mine, "--warmup", str(WARMUP)]
            procs.append((subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           text=True, cwd=ROOT), res))
        for p, _ in procs:  # warm-up done on every connection
            if p.stdout.readline().strip() != "READY":
                raise RuntimeError("load generator failed before READY")
        c0, w0 = gate.ps.cpu_times(), time.perf_counter()
        if profile:
            gate.p.send_signal(signal.SIGUSR1)
        for p, _ in procs:
            p.stdin.write("GO\n"); p.stdin.flush()
        for p, _ in procs:
            p.wait()
        c1, w1 = gate.ps.cpu_times(), time.perf_counter()
        if profile:
            gate.p.send_signal(signal.SIGUSR2)
            time.sleep(0.5)
        res = [json.load(open(r)) for _, r in procs]
        epi = os.path.join(outdir, "epi.json")
        subprocess.run([PY, "-m", "bench.loadgen", "--port", str(gate.port), "--workload", wl,
                        "--expected", wl.replace(".jsonl", ".expected.json"), "--result", epi,
                        "--mode", "closed", "--epilogue"], check=True, cwd=ROOT)
        res.append(json.load(open(epi)))
    finally:
        gate.stop()
    window = (max(r["t1"] for r in res[:-1]) - min(r["t0"] for r in res[:-1])) / 1e9
    measured = sum(r["measured"] for r in res[:-1])
    rec.update(orders_per_sec=measured / window, measured_orders=measured, window_s=window,
               gate_cpu_pct=100 * ((c1.user + c1.system) - (c0.user + c0.system)) / (w1 - w0),
               gate_peak_rss_mb=gate.peak / 2**20)
    if mode == "closed":
        lat = sorted(x for r in res[:-1] for x in r["lat_ns"])
        rec.update(lat_us={"p50": pct(lat, 500), "p99": pct(lat, 990), "p99.9": pct(lat, 999),
                           "max": lat[-1] / 1000, "samples": len(lat)})
    # verification: a run that fails is invalid and not reported in the tables
    if sum(r["errors"] for r in res):
        rec["errors"].append("error replies from gate")
    if sum(r["mismatches"] for r in res):
        rec["errors"].append(f"{sum(r['mismatches'] for r in res)} client-side decision mismatches")
    t = time.perf_counter()
    a = audit(log, load_rules(rules))
    replay_s = time.perf_counter() - t
    size = os.path.getsize(log)
    if a.chain_error:
        rec["errors"].append(f"chain: {a.chain_error}")
    if a.findings:
        rec["errors"].append(f"{len(a.findings)} auditor findings")
    if (a.events, a.accepted) != (expected["n"], expected["accepted"]):
        rec["errors"].append(f"events/accepted {a.events}/{a.accepted} != {expected['n']}/{expected['accepted']}")
    reasons = {k: v for k, v in expected["by_reason"].items() if k != "OK"}
    if dict(a.rejects) != reasons:
        rec["errors"].append(f"reject split {dict(a.rejects)} != {reasons}")
    rec.update(valid=not rec["errors"], log_bytes_per_event=size / max(a.events, 1),
               replay_events_per_sec=a.events / replay_s, accepted=a.accepted,
               rejects=dict(a.rejects))
    if profile:
        rec["profile_file"] = os.path.join(outdir, "gate.prof")
        rec["profile"] = analyze(rec["profile_file"])
    if rec["valid"] and not profile:
        for f in os.listdir(outdir):
            if f.endswith(".log"):
                os.remove(os.path.join(outdir, f))  # keep disk use bounded
    return rec


def recovery(sizes, rules_path, reps):
    out = []
    empty = []
    for r in range(reps):  # baseline: interpreter + imports + empty log
        d = os.path.join(RUNS, f"rec_empty_{r}"); os.makedirs(d, exist_ok=True)
        g = GateProc(rules_path, os.path.join(d, "g.log"), "none", d); empty.append(g.listening); g.stop()
    base = statistics.median(empty)
    for size in sizes:
        log = os.path.join(workload.DATA, f"log_{size}.log")
        if not os.path.exists(log):
            workload.generate(size, 7, log, keep=False)
        times, rss, ok = [], [], True
        for r in range(reps):
            d = os.path.join(RUNS, f"rec_{size}_{r}"); os.makedirs(d, exist_ok=True)
            g = GateProc(rules_path, log, "none", d)
            time.sleep(0.2)
            ok &= g.replayed() == size
            times.append(g.listening); rss.append(g.peak / 2**20); g.stop()
        t = time.perf_counter(); a = audit(log, load_rules(rules_path)); at = time.perf_counter() - t
        ok &= a.chain_error is None and a.events == size
        out.append({"events": size, "log_mb": os.path.getsize(log) / 2**20, "valid": bool(ok),
                    "startup_s": times, "baseline_empty_log_s": base,
                    "recovery_s_median": statistics.median(times) - base,
                    "peak_rss_mb": max(rss), "auditor_replay_events_per_sec": size / at})
        print(f"recovery {size:>9}: {statistics.median(times):.2f}s startup "
              f"(-{base:.2f}s baseline) rss {max(rss):.0f} MB valid={ok}", flush=True)
    return out


def summarize(runs):
    """Median and min/max over valid reps per configuration."""
    groups = {}
    for r in runs:
        groups.setdefault((r["fsync"], r["mode"], r["conns"]), []).append(r)
    rows = []
    for (fs, mode, c), rs in groups.items():
        v = [r for r in rs if r["valid"]]
        row = {"fsync": fs, "mode": mode, "conns": c, "valid_reps": len(v), "total_reps": len(rs)}
        if v:
            def stat(get):
                xs = [get(r) for r in v]
                return {"median": statistics.median(xs), "min": min(xs), "max": max(xs)}
            row["orders_per_sec"] = stat(lambda r: r["orders_per_sec"])
            row["gate_cpu_pct"] = stat(lambda r: r["gate_cpu_pct"])
            row["gate_peak_rss_mb"] = stat(lambda r: r["gate_peak_rss_mb"])
            row["log_bytes_per_event"] = stat(lambda r: r["log_bytes_per_event"])
            row["replay_events_per_sec"] = stat(lambda r: r["replay_events_per_sec"])
            if mode == "closed":
                for k in ("p50", "p99", "p99.9", "max"):
                    row["lat_" + k] = stat(lambda r, k=k: r["lat_us"][k])
        rows.append(row)
    return rows


def fmt_row(r):
    if not r["valid_reps"]:
        return f"{r['fsync']:<9} {r['mode']:<9} {r['conns']:>2}  NO VALID RUNS"
    o = r["orders_per_sec"]
    lat = (f" p50 {r['lat_p50']['median']:.0f} p99 {r['lat_p99']['median']:.0f} "
           f"p99.9 {r['lat_p99.9']['median']:.0f} max {r['lat_max']['median']:.0f} us") if "lat_p50" in r else ""
    return (f"{r['fsync']:<9} {r['mode']:<9} {r['conns']:>2}  {o['median']:>9.0f}/s "
            f"[{o['min']:.0f}-{o['max']:.0f}] cpu {r['gate_cpu_pct']['median']:>3.0f}% "
            f"rss {r['gate_peak_rss_mb']['median']:.0f} MB{lat}  ({r['valid_reps']}/{r['total_reps']} valid)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", type=int, default=100_000)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--fsync", nargs="+", default=FSYNCS)
    ap.add_argument("--modes", nargs="+", default=MODES)
    ap.add_argument("--conns", nargs="+", type=int, default=CONNS)
    ap.add_argument("--recovery", nargs="*", type=int, default=[10_000, 100_000, 1_000_000])
    ap.add_argument("--no-profile", action="store_true")
    ap.add_argument("--label", default="run")
    a = ap.parse_args()
    os.makedirs(RUNS, exist_ok=True); os.makedirs(RESULTS, exist_ok=True)
    rules = os.path.join(workload.DATA, "rules_bench.yaml")
    os.makedirs(workload.DATA, exist_ok=True); workload.write_rules(rules)
    wl, expected = workload.load(a.orders)
    environment = env.capture(RUNS)
    print(f"workload {a.orders}: accepted={expected['accepted']} rejected={expected['rejected']} "
          f"{expected['by_reason']}", flush=True)
    configs = [(f, m, c) for f in a.fsync for m in a.modes for c in a.conns]
    runs = []
    for rep in range(a.reps):  # reps interleaved across configs to spread machine drift
        for cfg in configs:
            rid = f"{a.label}_{cfg[0]}_{cfg[1]}_{cfg[2]}_r{rep}"
            rec = run_once(cfg, a.orders, rules, wl, expected, rid)
            rec["rep"] = rep
            runs.append(rec)
            print(f"[{'ok ' if rec['valid'] else 'INVALID'}] {rid}: {rec['orders_per_sec']:.0f}/s "
                  f"{rec['errors'] or ''}", flush=True)
    profiles = []
    if not a.no_profile:
        for fs in ("per_order", "batched"):
            rec = run_once((fs, "pipelined", 8), a.orders, rules, wl, expected, f"{a.label}_profile_{fs}",
                           profile=True)
            profiles.append(rec)
            print(f"[{'ok ' if rec['valid'] else 'INVALID'}] profile {fs}", flush=True)
    rec_rows = recovery(a.recovery, rules, a.reps) if a.recovery else []
    summary = summarize(runs)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(RESULTS, f"{a.label}_{stamp}.json")
    json.dump({"label": a.label, "orders": a.orders, "reps": a.reps, "warmup": WARMUP,
               "environment": environment, "workload": {k: v for k, v in expected.items() if k != "decisions"},
               "runs": runs, "summary": summary, "profiles": profiles, "recovery": rec_rows,
               "command": " ".join(sys.argv)}, open(path, "w"), indent=1, default=str)
    print("\n== summary (median [min-max] of valid reps; fsync=none is NON-DURABLE) ==")
    for r in summary:
        print(fmt_row(r) + ("   <-- NON-DURABLE" if r["fsync"] == "none" else ""))
    bad = [r["id"] for r in runs + profiles if not r["valid"]]
    print(f"\nresults: {path}\nfailed/invalid runs: {bad or 'none'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
