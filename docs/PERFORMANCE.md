# Performance

Simulated trading only. Every number below was measured by this repo's benchmark on the machine described and is read from `bench/results/` — nothing is estimated. There are **two separately labelled sets of runs**: **Native** (plain host process, **no CPU or memory limits enforced**) and **Docker** (gate in a container limited to `--cpus=1 --memory=512m`, on macOS inside Docker Desktop's Linux VM). Do not mix their numbers. Commit hashes in the result files are pre-rebase; see **Commit hash mapping** near the end.

## Workload (both sections)

- 100,000 orders, seed 7, unique `client_order_id`s, 32 symbols plus one epilogue symbol.
- Reference model (core's `check()` run through `Book`, in `bench/workload.py`): **83,840 ACCEPT (83.8%)** and **16,160 REJECT** (LOSS_STREAK_COOLDOWN 300, MAX_POSITION 8,079, PRICE_BAND 7,781).
- Mix: ~8% outside the price band, ~8% oversized for the position limit, the rest normal flow biased back toward flat so positions hover near (and sometimes hit) the limit. The last 306 orders are a sequential epilogue: three losing round trips trigger the loss-streak cooldown, then 300 risk-increasing orders are rejected by it.
- Benchmark rules (`bench/data/rules_bench.yaml`, generated): rate limit and daily-loss limit are set so high they never fire, and the cooldown lasts 86,400 s. This makes every decision independent of how orders from different connections interleave, so the expected totals are exact at any connection count. Rate limit and daily loss are covered by unit tests, not by this benchmark.
- A connection owns whole symbols (`symbol index % connections`), so order within a symbol is preserved.

## Metrics and why they matter

| Metric | Why it matters |
|---|---|
| Latency p50 / p99 / p99.9 / max (closed loop) | What a trader waits for a decision. One order in flight per connection, timed client-side from send to reply, avoids coordinated omission. |
| Orders/sec (pipelined and closed) | Peak the single writer can sustain. |
| Gate CPU %, peak RSS | Shows whether the gate or the load generator is the limit, and memory headroom. |
| Log bytes per event | Disk growth and replay cost. |
| Auditor replay events/sec | How fast an audit or a recovery can re-derive state. |
| Startup / recovery time vs log size | How long the gate is down after a crash. |


## Procedure

```
make setup
make bench BENCH_N=100000 BENCH_REPS=3   # native: python -m bench.run --orders 100000 --reps 3 --label final && python -m bench.report
docker build -t trade-seatbelt-gate .       # image used by the Docker runs
python -m bench.run --backend docker --orders 100000 --reps 3 --label docker   # (make bench-docker)
```

- Every run starts a **fresh gate (process or container) and a fresh log**. Docker: fresh container and fresh named volume.
- Warm-up: the first 5,000 orders of each run are sent first on the same connections, fully drained, and excluded from all statistics (they are still in the log and verified).
- Closed loop: one order in flight per connection, latency timed with `perf_counter_ns` in the load generator. Pipelined: up to 256 in flight per connection, used for throughput only.
- Connections 1, 8, 32; fsync `per_order`, `batched`, `none`; each configuration run 3 times, interleaved across configurations; tables show median [min–max]. The load generator is a separate process set (up to 4), never inside the gate and never inside the container.
- Orders/sec = measured orders / (last reply − first send) across all load-generator processes.
- **Verification after every run:** the auditor verifies the chain, replays the log and re-checks every decision with `check()`; the run is **invalid** and excluded unless event count, accept total and reject split equal the reference model's, there are no auditor findings, and the load generator saw no per-order decision mismatch (and, in Docker, the container was not OOM-killed). Docker logs are copied out of the container's volume before auditing.
- Recovery: a gate is started on pre-built logs of 10k / 100k / 1M events (`bench/workload.py --emit-log`); time from start to the `listening` line, minus the same measurement on an empty log.
- Profiling (native only): separate runs with `cProfile` enabled in the gate only during the measured phase; it inflates Python-level time, so shares are indicative and those runs are not used for throughput.

## Native results (no limits enforced)

### Environment (native)

| Item | Value |
|---|---|
| CPU | Apple M5 (10 physical / 10 logical cores) |
| RAM | 16 GiB |
| OS | Darwin 26.7 (arm64) |
| Python / uvloop | 3.12.15 / 0.23.0 |
| Limits | **NONE enforced.** The gate ran as a plain host process: no CPU or memory limit. |
| Log filesystem | `/dev/disk3s5   460Gi   102Gi   327Gi    24%    783k  3.4G    0%   /System/Volumes/Data` |
| Mount | `/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse, protect, root data)` |
| Power | Now drawing from 'Battery Power';  -InternalBattery-0 (id=22806627)	83%; discharging; 14:36 remaining present: true |
| Git commit | `fd47adb76d18565893d837c120d0b8c324bdd88c` |

The gate (one process, one thread in use) and up to 4 load-generator processes shared the same machine, so nothing stopped them competing for CPU. fsync here is native macOS `os.fsync` on APFS, which asks the OS to flush to the drive but does not force the drive's own cache (`F_FULLFSYNC`).

#### Closed-loop latency (µs) and throughput

| fsync | conns | p50 | p99 | p99.9 | max | orders/s | valid |
|---|---|---|---|---|---|---|---|
| per_order | 1 | 85 [84–86] | 108 [103–110] | 191 [121–242] | 14555 [14390–14730] | 11313 [11139–11467] | 3/3 |
| per_order | 8 | 365 [359–365] | 440 [416–664] | 794 [666–1676] | 15010 [14891–15137] | 21422 [20918–21764] | 3/3 |
| per_order | 32 | 1404 [1372–1417] | 1960 [1908–2011] | 6127 [5940–8776] | 16636 [16588–16705] | 21042 [21036–21400] | 3/3 |
| batched | 1 | 84 [84–87] | 102 [102–113] | 119 [116–278] | 14493 [14340–14667] | 11523 [11027–11601] | 3/3 |
| batched | 8 | 271 [271–272] | 331 [316–336] | 683 [554–731] | 14938 [14828–15726] | 28543 [28490–28600] | 3/3 |
| batched | 32 | 832 [830–833] | 1232 [1222–1266] | 5433 [5418–5785] | 15838 [15682–15867] | 35450 [35055–35484] | 3/3 |
| none (NON-DURABLE) | 1 | 72 [72–74] | 89 [89–97] | 108 [105–201] | 14392 [14356–14651] | 13169 [12775–13191] | 3/3 |
| none (NON-DURABLE) | 8 | 219 [218–220] | 270 [262–280] | 535 [513–556] | 15095 [15092–15599] | 35125 [35111–35562] | 3/3 |
| none (NON-DURABLE) | 32 | 789 [781–806] | 1117 [1082–1176] | 5682 [5496–6065] | 16199 [15724–17245] | 37406 [36714–37794] | 3/3 |

Median [min–max] over valid reps. About 94,694 samples per run, so p99.9 rests on ~94 observations and `max` is a single observation per run.

#### Pipelined throughput and gate resources

| fsync | conns | orders/s | gate CPU % | peak RSS MB | valid |
|---|---|---|---|---|---|
| per_order | 1 | 34013 [33395–34365] | 63 [63–63] | 201 [201–201] | 3/3 |
| per_order | 8 | 34488 [34278–36265] | 66 [64–66] | 202 [202–202] | 3/3 |
| per_order | 32 | 34883 [34791–35200] | 66 [64–67] | 203 [203–203] | 3/3 |
| batched | 1 | 90803 [89363–91361] | 96 [95–96] | 201 [201–201] | 3/3 |
| batched | 8 | 109094 [102246–109664] | 97 [96–98] | 202 [202–202] | 3/3 |
| batched | 32 | 107506 [100533–109582] | 98 [97–98] | 203 [203–203] | 3/3 |
| none (NON-DURABLE) | 1 | 88931 [87971–89246] | 99 [99–99] | 201 [201–201] | 3/3 |
| none (NON-DURABLE) | 8 | 110430 [109048–110772] | 98 [98–98] | 202 [202–202] | 3/3 |
| none (NON-DURABLE) | 32 | 107395 [107201–107538] | 98 [98–98] | 203 [203–204] | 3/3 |


#### Gate resources, closed loop

| fsync | conns | gate CPU % | peak RSS MB |
|---|---|---|---|
| per_order | 1 | 46 [45–46] | 201 [201–201] |
| per_order | 8 | 79 [78–79] | 201 [201–201] |
| per_order | 32 | 79 [79–79] | 201 [201–201] |
| batched | 1 | 45 [45–47] | 201 [201–201] |
| batched | 8 | 89 [89–89] | 201 [201–201] |
| batched | 32 | 97 [96–97] | 201 [201–201] |
| none (NON-DURABLE) | 1 | 45 [45–45] | 201 [201–201] |
| none (NON-DURABLE) | 8 | 99 [99–99] | 201 [201–201] |
| none (NON-DURABLE) | 32 | 99 [99–99] | 201 [201–201] |

Native: CPU % = gate process CPU time / wall time around the measured phase (`psutil`); peak RSS sampled every 20 ms.

#### Log size and replay

- Log bytes per event: median 353 (min 353, max 353) over 54 valid runs.
- Auditor replay (run natively on the host in both sections): median 157,138 events/s (min 149,488, max 162,950).

#### Startup / recovery time vs log size

| events | log MB | startup s (reps) | recovery s (minus empty-log baseline) | peak RSS MB | auditor replay events/s | valid |
|---|---|---|---|---|---|---|
| 10,000 | 3 | 0.17, 0.20, 0.20 | 0.08 | 56 | 128,076 | True |
| 100,000 | 34 | 0.67, 0.69, 0.67 | 0.55 | 211 | 149,244 | True |
| 1,000,000 | 338 | 6.41, 6.35, 6.25 | 6.23 | 1749 | 143,758 | True |

Empty-log baseline (interpreter + imports): 0.12 s.

#### Where the writer's time goes (cProfile, pipelined, 8 connections)

| Category | per_order (% of profiled time) | batched (% of profiled time) |
|---|---|---|
| fsync | 39.4 | 1.1 |
| log write syscall | 6.8 | 0.3 |
| sha256 hashing | 1.1 | 2.0 |
| orjson + pydantic (parse, validate, dump) | 11.3 | 21.9 |
| socket send (StreamWriter.write) | 0.4 | 0.8 |
| check / book / ledger code | 14.6 | 26.5 |
| gate engine + server code | 9.6 | 16.2 |
| asyncio queues, streams, tasks (Python) | 9.3 | 16.9 |
| event loop core, socket reads, idle | 7.5 | 14.3 |

fsync calls in the profiled window: per_order: 94,694, batched: 437.

### The one optimization: before and after (native)

Both sets measured natively at 20,000 orders, 3 reps, on the same machine on the same day (before: commit `7261814f8ec9`, after: `fd47adb76d18`). Change: the connection handler writes all already-completed responses to the socket in one call instead of one call per response (`gate/server.py`). Chosen because the profile showed socket sends as the largest single cost under `batched`. Nothing else was tuned.

| fsync | mode | conns | orders/s before | orders/s after | change % | p50 µs before→after |
|---|---|---|---|---|---|---|
| per_order | closed | 1 | 11497 [11451–11813] | 11269 [11249–11413] | -2 | 84→86 |
| per_order | closed | 8 | 21099 [20931–22339] | 21427 [20490–21515] | +2 | 371→365 |
| per_order | closed | 32 | 21429 [21080–23056] | 21959 [20749–21966] | +2 | 1380→1336 |
| per_order | pipelined | 1 | 24093 [23603–24214] | 35602 [35417–35813] | +48 |  |
| per_order | pipelined | 8 | 25522 [22395–25782] | 35779 [34136–36112] | +40 |  |
| per_order | pipelined | 32 | 24117 [23539–24581] | 35354 [34406–36655] | +47 |  |
| batched | closed | 1 | 11237 [11202–11438] | 11362 [10749–11453] | +1 | 86→84 |
| batched | closed | 8 | 28509 [28104–28580] | 27782 [26684–27823] | -3 | 272→279 |
| batched | closed | 32 | 34675 [34277–34755] | 34689 [34144–34796] | +0 | 838→841 |
| batched | pipelined | 1 | 43210 [42549–43448] | 94642 [94174–95733] | +119 |  |
| batched | pipelined | 8 | 44439 [43872–44841] | 110196 [107270–111025] | +148 |  |
| batched | pipelined | 32 | 44063 [41802–44374] | 107539 [104262–109510] | +144 |  |
| none (NON-DURABLE) | closed | 1 | 12930 [12869–12996] | 12717 [12685–12891] | -2 | 74→74 |
| none (NON-DURABLE) | closed | 8 | 35821 [34207–35898] | 35501 [34524–35761] | -1 | 216→218 |
| none (NON-DURABLE) | closed | 32 | 37629 [36343–37795] | 37358 [36698–37723] | -1 | 778→784 |
| none (NON-DURABLE) | pipelined | 1 | 44615 [44336–44741] | 92803 [91728–93111] | +108 |  |
| none (NON-DURABLE) | pipelined | 8 | 45632 [45526–45720] | 111867 [107874–113339] | +145 |  |
| none (NON-DURABLE) | pipelined | 32 | 44546 [44142–44605] | 107303 [94730–109267] | +141 |  |


### fsync call on macOS: `os.fsync` vs `F_FULLFSYNC` (native, 20k orders)

**Which one the headline native numbers used:** plain `os.fsync` (the gate's default; `fcntl.F_FULLFSYNC` is opt-in via `--full-fsync`). Everything in the other native tables and in the Docker section used the default call.

What each actually gives on macOS: `os.fsync` hands the data to the drive but does not ask the drive to flush its own volatile cache, so an acknowledged order can still be lost on power failure; `F_FULLFSYNC` asks the drive to flush its cache to permanent storage, which is the stronger guarantee and is what a power-loss claim would need. We did not test power loss, so neither guarantee was verified by crashing a machine.

Microbenchmark (`bench/fsync_micro.py`, 300 append+sync calls of 330 bytes, same volume): `os.fsync` median 19 µs (p99 36); `F_FULLFSYNC` median 3942 µs (p99 5074) — about 208× more.

Both sets: 20,000 orders, 3 reps, run back to back on the same machine (plain first). Only `per_order` and `batched` are shown; fsync off is unaffected.

| fsync | mode | conns | orders/s `os.fsync` | orders/s `F_FULLFSYNC` | change | p50 µs | p99 µs |
|---|---|---|---|---|---|---|---|
| per_order | closed | 1 | 11416 [11251–11429] | 250 [249–267] | -98% | 85 → 3983 | 112 → 5031 |
| per_order | closed | 8 | 21032 [20536–21562] | 251 [250–274] | -99% | 372 → 31982 | 440 → 37552 |
| per_order | closed | 32 | 21815 [21374–21917] | 280 [277–289] | -99% | 1344 → 113065 | 1931 → 184518 |
| per_order | pipelined | 1 | 35194 [32329–36015] | 287 [268–290] | -99% |  |  |
| per_order | pipelined | 8 | 36183 [35828–36350] | 287 [279–289] | -99% |  |  |
| per_order | pipelined | 32 | 36688 [36585–36895] | 292 [271–303] | -99% |  |  |
| batched | closed | 1 | 11403 [11396–11525] | 254 [250–260] | -98% | 84 → 3977 | 104 → 4937 |
| batched | closed | 8 | 28083 [28078–28144] | 654 [647–674] | -98% | 276 → 11976 | 324 → 13168 |
| batched | closed | 32 | 34841 [34514–34890] | 2445 [2319–2454] | -93% | 836 → 11974 | 1111 → 18808 |
| batched | pipelined | 1 | 96396 [96283–96940] | 15777 [13467–22475] | -84% |  |  |
| batched | pipelined | 8 | 111740 [110698–111977] | 32920 [29518–35864] | -71% |  |  |
| batched | pipelined | 32 | 110069 [109672–111133] | 38979 [38211–72521] | -65% |  |  |

Invalid runs in this comparison: none.

## Docker results (gate limited to 1 CPU, 512 MB; macOS Docker Desktop VM)

### Environment (Docker)

| Item | Value |
|---|---|
| Host | Apple M5, 16 GiB, Darwin 26.7 (arm64) (same machine as the native runs) |
| Docker | Docker version 29.8.2, build 7fc2dff |
| Docker Desktop VM | Linux 7.0.14-linuxkit aarch64, **10 CPUs, 7.75 GiB RAM** (what the VM was given) |
| Docker Desktop settings file | `{'DisplayedOnboarding': True}` — it holds no CPU/memory override, so the VM allocation above is Docker Desktop's default for this machine |
| Container limits (verified) | `docker inspect`: NanoCpus=1000000000 (= 1 CPU), Memory=536870912 (= 512 MiB), MemorySwap=536870912 (= no swap beyond the limit). Inside the container, cgroup `cpu.max` = `100000 100000`, `memory.max` = `536870912` |
| Second limit | The VM's own CPU and RAM are a limit on top of the container's. The container could never use more than the VM has; here the VM is larger than the container limit, so the container limit binds. |
| Log filesystem | `/dev/vda1       453G  1.5G  428G   1% /data` mounted as `/dev/vda1 /data ext4 rw,relatime,discard 0 0` — a Docker named volume on the **VM's disk** |
| Load generator | **Outside the container**, on the macOS host (up to 4 processes). Processes inside the container at the end of the first run: `python -m gate --host 0.0.0.0 --port 9101 --rules rules_bench.yaml --log /data/gate.log --fsync per_order` |
| Network path | Host to container goes through Docker Desktop's published port, which adds latency and host-side CPU that the native runs do not have. |
| Git commit | `fd47adb76d18565893d837c120d0b8c324bdd88c (+uncommitted changes)` — the flag means bench-harness files were being edited during the run; `gate/` and `core/` in the image were identical to that commit (the image was built from them before any later edit) |

**fsync caveat:** on macOS, Docker runs inside a Linux VM, so fsync cost here reflects the *VM's* disk (ext4 on a virtual block device backed by a file on the host) and is **not** the same as native macOS fsync. How far an fsync in the VM reaches the host's physical drive was not measured. A bind mount of a host directory was deliberately not used, because it would send fsync through virtiofs to the host filesystem instead.

#### Closed-loop latency (µs) and throughput

| fsync | conns | p50 | p99 | p99.9 | max | orders/s | valid |
|---|---|---|---|---|---|---|---|
| per_order | 1 | 634 [563–635] | 894 [815–995] | 1833 [1362–2381] | 23939 [16416–34021] | 1573 [1544–1719] | 3/3 |
| per_order | 8 | 2503 [2339–2540] | 3814 [2841–4617] | 7843 [4819–12514] | 29816 [22648–41795] | 3125 [3035–3576] | 3/3 |
| per_order | 32 | 9768 [9241–9850] | 13853 [10853–16218] | 31568 [22002–33758] | 39273 [25943–47218] | 3187 [3174–3794] | 3/3 |
| batched | 1 | 560 [556–727] | 1673 [1652–2730] | 3020 [2889–4326] | 96571 [61193–118377] | 1497 [1117–1570] | 3/3 |
| batched | 8 | 865 [861–971] | 1464 [1414–1609] | 3445 [3321–3568] | 15604 [15593–17752] | 8900 [7804–9121] | 3/3 |
| batched | 32 | 1901 [1890–1944] | 2902 [2808–3068] | 6706 [6669–11726] | 18456 [18219–18861] | 16181 [15600–16287] | 3/3 |
| none (NON-DURABLE) | 1 | 313 [267–320] | 403 [381–423] | 618 [599–1217] | 15002 [14760–27435] | 3257 [3256–3605] | 3/3 |
| none (NON-DURABLE) | 8 | 436 [436–446] | 712 [707–733] | 1455 [1340–1554] | 16032 [15843–16034] | 17714 [17252–17720] | 3/3 |
| none (NON-DURABLE) | 32 | 1084 [1082–1162] | 1890 [1811–2790] | 6082 [5810–16667] | 17388 [17254–21869] | 28466 [25640–28634] | 3/3 |

Median [min–max] over valid reps. About 94,694 samples per run, so p99.9 rests on ~94 observations and `max` is a single observation per run.

#### Pipelined throughput and gate resources

| fsync | conns | orders/s | gate CPU % | peak RSS MB | CPU throttled ms | valid |
|---|---|---|---|---|---|---|
| per_order | 1 | 2332 [2324–3866] | 7 [7–10] | 196 [196–196] |  | 3/3 |
| per_order | 8 | 1321 [1279–2256] | 6 [4–6] | 197 [197–197] |  | 3/3 |
| per_order | 32 | 1727 [1238–2409] | 5 [4–6] | 198 [198–199] |  | 3/3 |
| batched | 1 | 50291 [48360–51758] | 61 [59–62] | 196 [196–196] |  | 3/3 |
| batched | 8 | 59466 [57881–64653] | 79 [77–80] | 197 [197–197] |  | 3/3 |
| batched | 32 | 63503 [63387–65346] | 81 [81–82] | 198 [198–198] |  | 3/3 |
| none (NON-DURABLE) | 1 | 58495 [58064–59295] | 88 [88–89] | 196 [196–196] |  | 3/3 |
| none (NON-DURABLE) | 8 | 76102 [62029–78322] | 100 [92–101] | 197 [197–197] |  | 3/3 |
| none (NON-DURABLE) | 32 | 75081 [73466–78400] | 99 [98–100] | 198 [198–198] |  | 3/3 |


#### Gate resources, closed loop

| fsync | conns | gate CPU % | peak RSS MB |
|---|---|---|---|
| per_order | 1 | 8 [8–10] | 196 [196–196] |
| per_order | 8 | 13 [12–13] | 196 [196–196] |
| per_order | 32 | 12 [12–13] | 196 [196–197] |
| batched | 1 | 7 [6–8] | 196 [196–197] |
| batched | 8 | 23 [22–24] | 196 [196–196] |
| batched | 32 | 32 [32–33] | 196 [196–196] |
| none (NON-DURABLE) | 1 | 13 [12–14] | 196 [196–196] |
| none (NON-DURABLE) | 8 | 49 [49–49] | 196 [196–196] |
| none (NON-DURABLE) | 32 | 79 [79–79] | 196 [196–196] |

Docker: CPU % = CPU seconds used (cgroup `cpu.stat`) / the load generator's measured window; peak RSS = the gate's `VmHWM`. 100% = the container's one CPU.

#### Log size and replay

- Log bytes per event: median 353 (min 353, max 353) over 54 valid runs.
- Auditor replay (run natively on the host in both sections): median 151,614 events/s (min 123,963, max 163,055).

#### Startup / recovery time vs log size

| events | log MB | startup s (reps) | recovery s (minus empty-log baseline) | peak RSS MB | auditor replay events/s | valid |
|---|---|---|---|---|---|---|
| 10,000 | 3 | 0.24, 0.25, 0.24 | 0.05 | 54 | 148,038 | True |
| 100,000 | 34 | 0.78, 0.79, 0.80 | 0.59 | 206 | 157,299 | True |
| 1,000,000 | 338 | — | **gate OOM_KILLED while replaying** | — | — | n/a |

Empty-log baseline (container create + start + interpreter + imports): 0.19 s. Docker timing polls `docker logs`, so it has about ±0.05 s resolution.

Profiling was not done in Docker (the cProfile hook is native-only).

## Native vs Docker, side by side

Same machine, same workload and seed, same load generator; only where the gate runs differs. Medians. Both sets use the same gate code (the post-optimization commit shown in each section).

| fsync | mode | conns | native orders/s | Docker orders/s | Docker vs native | p50 µs native → Docker |
|---|---|---|---|---|---|---|
| per_order | closed | 1 | 11,313 | 1,573 | -86% | 85 → 634 |
| per_order | closed | 8 | 21,422 | 3,125 | -85% | 365 → 2503 |
| per_order | closed | 32 | 21,042 | 3,187 | -85% | 1404 → 9768 |
| per_order | pipelined | 1 | 34,013 | 2,332 | -93% |  |
| per_order | pipelined | 8 | 34,488 | 1,321 | -96% |  |
| per_order | pipelined | 32 | 34,883 | 1,727 | -95% |  |
| batched | closed | 1 | 11,523 | 1,497 | -87% | 84 → 560 |
| batched | closed | 8 | 28,543 | 8,900 | -69% | 271 → 865 |
| batched | closed | 32 | 35,450 | 16,181 | -54% | 832 → 1901 |
| batched | pipelined | 1 | 90,803 | 50,291 | -45% |  |
| batched | pipelined | 8 | 109,094 | 59,466 | -45% |  |
| batched | pipelined | 32 | 107,506 | 63,503 | -41% |  |
| none (NON-DURABLE) | closed | 1 | 13,169 | 3,257 | -75% | 72 → 313 |
| none (NON-DURABLE) | closed | 8 | 35,125 | 17,714 | -50% | 219 → 436 |
| none (NON-DURABLE) | closed | 32 | 37,406 | 28,466 | -24% | 789 → 1084 |
| none (NON-DURABLE) | pipelined | 1 | 88,931 | 58,495 | -34% |  |
| none (NON-DURABLE) | pipelined | 8 | 110,430 | 76,102 | -31% |  |
| none (NON-DURABLE) | pipelined | 32 | 107,395 | 75,081 | -30% |  |


## Commit hash mapping

The commit hashes recorded in the raw result files (and shown in the tables above) are from **before** the history was rebased onto the GitHub repository's initial `LICENSE` commit (`6781848`). The raw result files are deliberately left unmodified, so they still show the old hashes. Commit hashes before and after `git pull --rebase origin main` (the GitHub repo already had a LICENSE commit). Matched by patch-id and checked against the reflog.

| Old hash (in result files) | New hash (in pushed history) | Commit |
|---|---|---|
| `0700ba1` | `5373dd6` | Stage 1: core, gate, auditor, client, fixtures |
| `7261814` | `cfa5ea2` | Bench harness, workload, load generator, rate-limit replay test |
| `fd47adb` | `67ff2a9` | Gate: coalesce completed responses into one socket write |
| `641f68f` | `388fec8` | Docs, Docker benchmark, F_FULLFSYNC option, durability tests |
| `0fdcd79` | `7b0c273` | Crash-recovery tests, torn-tail repair, safe bench output, license check |

Each new hash was checked with `git cat-file -t` (all are commits) and `git merge-base --is-ancestor <hash> origin/main` (all are in the pushed history); the old hashes are not reachable from it.

## Code changes after the benchmarked commit

Every native run in the main tables, and the code in the Docker image, is from commit `fd47adb` (now `67ff2a9`). What changed in `core/` and `gate/` since then (checked with `git diff 67ff2a9 HEAD` at `7b0c273`):

| Change | Where | When it runs |
|---|---|---|
| Opt-in `--full-fsync` (`F_FULLFSYNC` instead of `os.fsync`; off by default) | `core/logio.py` (`LogWriter`), parameter plumbing in `gate/engine.py` and `gate/__main__.py` | only if the flag is given |
| Torn-tail truncation: a partial last line is removed, kept in a sidecar file and noted | `core/logio.py` (`repair_torn_tail`, new), called from `Gate.start()` in `gate/engine.py` | once, at startup |
| Gate output lines: `torn_tail_bytes_removed=` at startup, `state_hash=` at shutdown, `+F_FULLFSYNC` tag | `gate/__main__.py` | startup / shutdown |

**The hot path is unchanged:** parse and validate (`Order`), dedupe, `check()`, event construction and hashing, `event_line`, the `LogWriter.write` `os.write` loop, the default `os.fsync(fd)` call, resolving the futures and the reply writer. `git diff 67ff2a9 HEAD` shows no change at all in `core/rules.py`, `core/book.py`, `core/ledger.py`, `core/canon.py`, `core/events.py`, `core/money.py`, `gate/server.py` or `auditor/`, and in `gate/engine.py` only the import line, the `Gate.__init__` parameters and `Gate.start()` differ (`submit`, `_handle`, `_run` and `_commit` are untouched).

The one difference on that path: `LogWriter.write` now tests `if self.full_fsync:` before its fsync call, i.e. one attribute check per fsync in the default configuration. The call itself is the same `os.fsync(self.fd)`.

**Not re-measured:** the 100k native and Docker numbers were not re-run on the final code. As a consistency check only (not proof), the 20k `os.fsync` run in the fsync section above was taken with the `--full-fsync` option present in the working tree (before the torn-tail change), and compares with the earlier 20k post-optimization run like this (orders/s, medians):

| fsync | mode | conns | 20k run before `--full-fsync` existed | 20k run with it present, off |
|---|---|---|---|---|
| batched | pipelined | 8 | 110,196 | 111,740 |
| per_order | pipelined | 8 | 35,779 | 36,183 |
| batched | closed | 1 | 11,362 | 11,403 |
| per_order | closed | 1 | 11,269 | 11,416 |

The torn-tail change came after every benchmark run and only executes at startup.

## Failures and skipped

- Invalid or failed native runs: none.
- Invalid or failed Docker runs: none.
- Power-loss durability (`F_FULLFSYNC`, real crash tests), native or Docker: **not measured**.

## Interpretation

### Observed bottleneck (native)

- Pipelined, 8 connections: 110,430 orders/s with fsync off, 109,094 with `batched`, 34,488 with `per_order`. Gate CPU is 98% / 97% / 66%, so in the first two the gate's single thread is saturated: that is the single-writer limit, not the load generator. With `per_order` the gate sat idle 34% of the time, waiting in fsync.
- Profile, `per_order`: fsync 39%; check / book / ledger code 15%; orjson + pydantic (parse, validate, dump) 11%.
- Profile, `batched`: check / book / ledger code 26%; orjson + pydantic (parse, validate, dump) 22%; asyncio queues, streams, tasks (Python) 17%.
- Native single-connection closed loop: p50 85 µs with `per_order` vs 72 µs with fsync off, so a native `os.fsync` costs about 12 µs there.

### What Docker changed

- Closed loop, 1 connection, fsync off: p50 72 µs native vs 313 µs in Docker. That ~241 µs is the cost of the Docker Desktop port-forwarding path, paid on every request.
- `per_order` fsync cost (p50 with fsync minus without): about 12 µs native vs 321 µs on the Docker VM's disk. These are different disks and different layers; the Docker figure must not be read as a native one.
- Pipelined, 8 connections: `per_order` 1,321/s and fsync-off 76,102/s in Docker (native: 34,488 and 110,430).
- CPU throttling by the 1-CPU limit: 4 ms in total over 54 valid runs (max 3 ms in one run); highest gate CPU 101% of the one allowed CPU.
- **Memory limit:** recovery of 1,000,000-event log(s) did not complete in the 512 MB container (gate OOM_KILLED). The same recovery natively needed the RSS shown in the native recovery table.

### Durability vs latency, per fsync policy

| Policy | When the client gets its reply | What can be lost on a crash |
|---|---|---|
| `per_order` | After that one event was written and fsynced. | Gate process killed: no acknowledged order is lost; an order with no reply yet may or may not be in the log. OS crash / power loss: acknowledged orders survive only as far as `os.fsync` is honoured by the drive (on macOS plain `os.fsync` is not forced through the drive cache; `--full-fsync` uses `F_FULLFSYNC` to do that — see the fsync section; power loss not tested here). |
| `batched` (group commit, up to 256 events per fsync) | After the whole batch was written and fsynced. | The same guarantee as `per_order` for acknowledged orders; it is cheaper because one fsync covers many events. A reply can wait for the rest of its batch. |
| `none` **(NON-DURABLE)** | After `os.write`, never fsynced. | Gate process killed: the bytes are already in the OS page cache, so they survive. OS crash / power loss: every event the OS had not yet flushed is lost, including acknowledged ones, and the last line may be partial. How much depends on OS writeback timing and was not measured. |

A crash in the middle of a write can leave a partial last line in any policy. On restart the gate removes only that partial line (it was never acknowledged, because replies follow the completed write), keeps the removed bytes in `<log>.torn.<ns>` and notes it in `<log>.recovery.jsonl`; any other corruption makes it refuse to start. Process crashes (`SIGKILL`) are tested; power loss is not.

### What would break or need redesign next

- **Memory and recovery time grow with log size.** Startup replays the whole log and keeps every event in the idempotency map: natively, peak RSS 1749 MB and 6.2 s recovery at 1,000,000 events; RSS first exceeded 512 MB at the 1,000,000-event log.
- **One core.** The single writer is one Python thread; once it is saturated, more connections add latency, not throughput (p50 grows with connections while orders/s stays flat in the tables).
- **Durability** beyond `os.fsync` is untested on both native macOS (`F_FULLFSYNC`) and the Docker VM disk; a power-loss claim needs real crash tests.
- **Not covered:** hours-long runs, network between machines, limits other than 1 CPU / 512 MB.
