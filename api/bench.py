"""Serve benchmark numbers exactly as stored in bench/results/*.json. Nothing is computed or estimated here."""

import json
from pathlib import Path

ENV_KEYS = ("cpu_model", "cpu_cores_physical", "cpu_cores_logical", "ram_bytes", "os", "python", "limits",
            "log_filesystem", "git_commit", "fsync_call")
ROW_KEYS = ("fsync", "mode", "conns", "valid_reps", "total_reps", "orders_per_sec", "lat_p50", "lat_p99",
            "lat_p99.9", "lat_max", "gate_cpu_pct", "gate_peak_rss_mb")


def _backend(d: dict) -> str:
    """The file's own `backend` field if it has one; older native files do not, so fall back to what the file
    says about its limits ('ENFORCED on the gate container' = Docker, 'NONE enforced: gate ran natively' = native)."""
    b = d.get("backend")
    if b in ("native", "docker"):
        return b
    limits = d.get("environment", {}).get("limits", "")
    return "docker" if limits.startswith("ENFORCED") else "native"


def load(results_dir: Path) -> dict:
    runs, micro = [], None
    for p in sorted(results_dir.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if "summary" in d:
            env = d.get("environment", {})
            runs.append({
                "file": p.name, "label": d.get("label"), "backend": _backend(d), "orders": d.get("orders"),
                "reps": d.get("reps"), "command": d.get("command"),
                "environment": {k: env[k] for k in ENV_KEYS if k in env},
                "workload": d.get("workload"),
                "rows": [{k: r[k] for k in ROW_KEYS if k in r} for r in d["summary"]],
                "recovery": d.get("recovery", []),
            })
        elif "os.fsync" in d:
            micro = {"file": p.name, **d}
    return {"runs": runs, "fsync_micro": micro, "units": {"orders_per_sec": "orders/s", "latency": "microseconds"},
            "source": "bench/results/*.json"}
