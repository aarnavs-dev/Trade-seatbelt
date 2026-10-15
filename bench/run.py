"""Benchmark harness: fresh gate + log per run, separate load-generator processes, verification
after every run. Writes raw results to bench/results/. Usage: python -m bench.run [--orders N]."""

import argparse
import json
import os
import shutil
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
             "--fsync", fsync] + (["--full-fsync"] if BACKEND.get("full_fsync") else []),
            stdout=open(self.outfile, "w"), stderr=subprocess.STDOUT,
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

    cpu_over_window = False  # CPU % uses the harness's own wall clock around the measured phase

    def cpu_seconds(self) -> float:
        t = self.ps.cpu_times()
        return t.user + t.system

    def signal(self, sig):
        self.p.send_signal(sig)

    def finish(self) -> None:
        pass

    def replayed(self) -> int:
        return int(open(self.outfile).read().split("replayed_events=")[1].split()[0])

    def stop(self):
        self._stop.set()
        self.p.send_signal(signal.SIGTERM)
        self.p.wait(timeout=60)


class GateDied(Exception):
    def __init__(self, msg, oom=False):
        super().__init__(msg)
        self.oom = oom


def sh(*cmd, check=True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


IMAGE = "trade-seatbelt-gate"
LIMITS = ["--cpus=1", "--memory=512m", "--memory-swap=512m"]  # swap = memory: no swapping past the limit


class DockerGate:
    """The gate in a container limited to 1 CPU / 512 MB. The load generator stays on the host.
    The log lives on a Docker named volume, i.e. the Docker Desktop VM's own disk (a macOS bind
    mount would send fsync through virtiofs to the host filesystem instead)."""

    cpu_over_window = True  # a `docker exec` read cannot be aligned to the phase, so divide by the load generator's window

    def __init__(self, rules, log, fsync, outdir, profile=None, preload=None, fetch_log=True):
        assert not profile, "profiling is native-only"
        self.port, self.outdir = free_port(), outdir
        self.name = "tsb-" + os.path.basename(outdir)
        self.vol, self.log = self.name + "-data", log
        self.peak, self.oom, self.throttled_us, self.fetch_log = 0, False, 0, fetch_log
        sh("docker", "volume", "create", self.vol)
        sh("docker", "create", "--name", self.name, *LIMITS, "-p", f"127.0.0.1:{self.port}:9101",
           "-v", f"{self.vol}:/data", "-v", f"{os.path.abspath(rules)}:/app/rules_bench.yaml:ro", IMAGE,
           "--rules", "rules_bench.yaml", "--log", "/data/gate.log", "--fsync", fsync)
        if preload:
            sh("docker", "cp", preload, f"{self.name}:/data/gate.log")
        self.t_spawn = time.perf_counter()
        sh("docker", "start", self.name)
        self.listening = None
        while self.listening is None:
            if "listening" in self._logs():
                self.listening = time.perf_counter() - self.t_spawn
                continue
            st = sh("docker", "inspect", "-f", "{{.State.Running}} {{.State.OOMKilled}}", self.name)
            if st.startswith("false"):
                self._cleanup()
                raise GateDied("container exited at startup: " + self._logs(), oom=st.endswith("true"))
            time.sleep(0.02)

    def _logs(self) -> str:
        r = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True)
        return r.stdout + r.stderr

    def _cgroup(self) -> dict:
        out = sh("docker", "exec", self.name, "python", "-c",
                 "print(open('/sys/fs/cgroup/cpu.stat').read())")
        return {k: int(v) for k, v in (l.split() for l in out.splitlines())}

    def cpu_seconds(self) -> float:
        st = self._cgroup()
        self._last_throttled = st["throttled_usec"]
        return st["usage_usec"] / 1e6

    def signal(self, sig):
        pass

    def describe(self) -> dict:
        ins = json.loads(sh("docker", "inspect", self.name))[0]["HostConfig"]
        return {
            "host_config": {"NanoCpus": ins["NanoCpus"], "Memory": ins["Memory"], "MemorySwap": ins["MemorySwap"]},
            "cgroup_cpu_max": sh("docker", "exec", self.name, "cat", "/sys/fs/cgroup/cpu.max"),
            "cgroup_memory_max": sh("docker", "exec", self.name, "cat", "/sys/fs/cgroup/memory.max"),
            "log_filesystem": sh("docker", "exec", self.name, "sh", "-c", "df -h /data | tail -1"),
            "log_mount": sh("docker", "exec", self.name, "sh", "-c", "grep ' /data ' /proc/mounts"),
            "processes_in_container": sh("docker", "top", self.name),
        }

    def finish(self) -> None:
        hwm = sh("docker", "exec", self.name, "grep", "VmHWM", "/proc/1/status")  # peak RSS of the gate (PID 1)
        self.peak = int(hwm.split()[1]) * 1024
        self.throttled_us = self._cgroup()["throttled_usec"]

    def replayed(self) -> int:
        return int(self._logs().split("replayed_events=")[1].split()[0])

    def _cleanup(self):
        sh("docker", "rm", "-f", "-v", self.name, check=False)
        sh("docker", "volume", "rm", "-f", self.vol, check=False)

    def stop(self):
        sh("docker", "stop", "-t", "60", self.name, check=False)
        self.oom = sh("docker", "inspect", "-f", "{{.State.OOMKilled}}", self.name, check=False) == "true"
        if self.fetch_log:
            sh("docker", "cp", f"{self.name}:/data/gate.log", self.log, check=False)  # for the auditor, on the host
        open(os.path.join(self.outdir, "gate.out"), "w").write(self._logs())
        self._cleanup()


BACKEND = {"gate": GateProc, "tag": "native"}


def new_gate(*a, **k):
    return BACKEND["gate"](*a, **k)


def pct(sorted_ns, q):
    return sorted_ns[max(0, -(-len(sorted_ns) * q // 1000) - 1)] / 1000  # q in permille, -> us


def run_once(cfg, n, rules, wl, expected, rid, profile=None):
    fsync, mode, conns = cfg
    outdir = os.path.join(RUNS, rid)
    shutil.rmtree(outdir, ignore_errors=True)  # a run starts on a fresh log: a leftover gate.log from an earlier run with this id would be replayed
    os.makedirs(outdir, exist_ok=True)
    log = os.path.join(outdir, "gate.log")
    rec = {"id": rid, "fsync": fsync, "mode": mode, "conns": conns, "n": n, "valid": False, "errors": []}
    gate = new_gate(rules, log, fsync, outdir, profile and os.path.join(outdir, "gate.prof"))
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
        c0, w0 = gate.cpu_seconds(), time.perf_counter()
        if profile:
            gate.signal(signal.SIGUSR1)
        for p, _ in procs:
            p.stdin.write("GO\n"); p.stdin.flush()
        for p, _ in procs:
            p.wait()
        c1, w1 = gate.cpu_seconds(), time.perf_counter()
        if profile:
            gate.signal(signal.SIGUSR2)
            time.sleep(0.5)
        res = [json.load(open(r)) for _, r in procs]
        epi = os.path.join(outdir, "epi.json")
        subprocess.run([PY, "-m", "bench.loadgen", "--port", str(gate.port), "--workload", wl,
                        "--expected", wl.replace(".jsonl", ".expected.json"), "--result", epi,
                        "--mode", "closed", "--epilogue"], check=True, cwd=ROOT)
        res.append(json.load(open(epi)))
        gate.finish()
        if BACKEND["gate"] is DockerGate and not BACKEND.get("described"):
            BACKEND["described"], rec["container"] = True, gate.describe()
    finally:
        gate.stop()
    if getattr(gate, "oom", False):
        rec["errors"].append("gate container was OOM-killed")
    window = (max(r["t1"] for r in res[:-1]) - min(r["t0"] for r in res[:-1])) / 1e9
    measured = sum(r["measured"] for r in res[:-1])
    rec.update(orders_per_sec=measured / window, measured_orders=measured, window_s=window,
               gate_cpu_pct=100 * (c1 - c0) / (window if gate.cpu_over_window else w1 - w0),
               gate_peak_rss_mb=gate.peak / 2**20)
    if gate.cpu_over_window:
        rec["gate_throttled_ms"] = gate.throttled_us / 1000
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
        rec["profile"] = analyze(os.path.join(outdir, "gate.prof"))
        rec["profile_file"] = os.path.relpath(os.path.join(outdir, "gate.prof"), ROOT)
    if rec["valid"] and not profile:
        for f in os.listdir(outdir):
            if f.endswith(".log"):
                os.remove(os.path.join(outdir, f))  # keep disk use bounded
    return rec


def recovery(sizes, rules_path, reps):
    docker = BACKEND["gate"] is DockerGate
    out, empty = [], []
    for r in range(reps):  # baseline: container/interpreter start + imports + empty log
        d = os.path.join(RUNS, f"{BACKEND['tag']}_rec_empty_{r}"); os.makedirs(d, exist_ok=True)
        g = new_gate(rules_path, os.path.join(d, "g.log"), "none", d); empty.append(g.listening); g.stop()
    base = statistics.median(empty)
    for size in sizes:
        log = os.path.join(workload.DATA, f"log_{size}.log")
        if not os.path.exists(log):
            workload.generate(size, 7, log, keep=False)
        row = {"events": size, "log_mb": os.path.getsize(log) / 2**20, "baseline_empty_log_s": base}
        times, rss, ok, died = [], [], True, None
        for r in range(reps):
            d = os.path.join(RUNS, f"{BACKEND['tag']}_rec_{size}_{r}"); os.makedirs(d, exist_ok=True)
            try:
                if docker:
                    g = new_gate(rules_path, os.path.join(d, "copy.log"), "none", d, preload=log, fetch_log=False)
                else:
                    g = new_gate(rules_path, log, "none", d)
            except GateDied as e:
                died = "OOM_KILLED" if e.oom else "EXITED"
                break
            time.sleep(0.2)
            ok &= g.replayed() == size
            g.finish()
            times.append(g.listening); rss.append(g.peak / 2**20); g.stop()
        if died:
            row.update(valid=True, outcome=died, startup_s=times,
                       note="gate did not finish replaying the log inside the limits")
            print(f"recovery {size:>9}: gate {died} during startup (limits: "
                  f"{'1 CPU / 512 MB' if docker else 'none'})", flush=True)
            out.append(row)
            continue
        t = time.perf_counter(); a = audit(log, load_rules(rules_path)); at = time.perf_counter() - t
        ok &= a.chain_error is None and a.events == size
        row.update(valid=bool(ok), outcome="OK", startup_s=times, recovery_s_median=statistics.median(times) - base,
                   peak_rss_mb=max(rss), auditor_replay_events_per_sec=size / at)
        out.append(row)
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
    ap.add_argument("--full-fsync", action="store_true", help="native macOS: F_FULLFSYNC instead of os.fsync")
    ap.add_argument("--backend", choices=["native", "docker"], default="native")
    a = ap.parse_args()
    os.makedirs(RUNS, exist_ok=True); os.makedirs(RESULTS, exist_ok=True)
    rules = os.path.join(workload.DATA, "rules_bench.yaml")
    os.makedirs(workload.DATA, exist_ok=True); workload.write_rules(rules)
    wl, expected = workload.load(a.orders)
    if a.backend == "docker":
        BACKEND.update(gate=DockerGate, tag="docker")
        a.no_profile = True  # cProfile hook is native-only
    BACKEND["full_fsync"] = a.full_fsync
    environment = env.capture(RUNS)
    environment["fsync_call"] = "fcntl F_FULLFSYNC" if a.full_fsync else "os.fsync"
    if a.backend == "docker":
        environment.update(env.capture_docker())
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
    if a.backend == "docker":
        c = next((r["container"] for r in runs if "container" in r), {})
        environment["container"] = c
        environment["limits"] = "ENFORCED on the gate container: --cpus=1 --memory=512m --memory-swap=512m"
        environment["log_filesystem"], environment["log_mount"] = c.get("log_filesystem", "?"), c.get("log_mount", "?")
    json.dump({"label": a.label, "backend": a.backend, "orders": a.orders, "reps": a.reps, "warmup": WARMUP,
               "environment": environment, "workload": {k: v for k, v in expected.items() if k != "decisions"},
               "runs": runs, "summary": summary, "profiles": profiles, "recovery": rec_rows,
               "command": "python -m bench.run " + " ".join(sys.argv[1:])}, open(path, "w"), indent=1, default=str)
    print("\n== summary (median [min-max] of valid reps; fsync=none is NON-DURABLE) ==")
    for r in summary:
        print(fmt_row(r) + ("   <-- NON-DURABLE" if r["fsync"] == "none" else ""))
    bad = [r["id"] for r in runs + profiles if not r["valid"]]
    print(f"\nresults: {path}\nfailed/invalid runs: {bad or 'none'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
