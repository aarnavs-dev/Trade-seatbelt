"""Capture the environment automatically so the report never relies on memory."""

import os
import platform
import shutil
import subprocess
import sys


def _run(*cmd) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def capture(log_dir: str) -> dict:
    import psutil
    import uvloop
    cpu = _run("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor()
    mount = next((l for l in _run("mount").splitlines() if l.split(" on ")[-1].startswith(
        _run("df", log_dir).splitlines()[-1].split()[-1] + " (")), "")
    docker = shutil.which("docker") and _run("docker", "--version")
    dirty = bool(_run("git", "status", "--porcelain", "--untracked-files=no"))
    return {
        "cpu_model": cpu, "cpu_cores_logical": os.cpu_count(),
        "cpu_cores_physical": psutil.cpu_count(logical=False),
        "ram_bytes": psutil.virtual_memory().total,
        "os": f"{platform.system()} {platform.mac_ver()[0] or platform.release()} ({platform.machine()})",
        "python": sys.version.split()[0], "uvloop": uvloop.__version__,
        "docker": docker or "not installed",
        "limits": "NONE enforced: gate ran natively, not in a container (no --cpus / --memory)",
        "log_filesystem": _run("df", "-h", log_dir).splitlines()[-1],
        "log_mount": mount or "unknown", "power": _run("pmset", "-g", "batt").splitlines()[0:2],
        "git_commit": _run("git", "rev-parse", "HEAD") + (" (+uncommitted changes)" if dirty else ""),
    }
