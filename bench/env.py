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


def capture_docker() -> dict:
    """Docker Desktop VM facts: the VM's CPU/RAM is a second limit on top of the container's."""
    import json as _json
    info = {}
    try:
        info = _json.loads(_run("docker", "info", "--format", "{{json .}}"))
    except ValueError:
        pass
    settings = {}
    path = os.path.expanduser("~/Library/Group Containers/group.com.docker/settings-store.json")
    if os.path.exists(path):
        raw = _json.load(open(path))
        settings = {k: raw[k] for k in ("Cpus", "MemoryMiB", "SwapMiB", "DiskSizeMiB", "UseVirtualizationFramework",
                                         "UseVirtualizationFrameworkVirtioFS", "VmType", "DisplayedOnboarding")
                    if k in raw}
    return {
        "docker_version": _run("docker", "--version"),
        "docker_vm": {"kernel": info.get("KernelVersion"), "os": info.get("OperatingSystem"),
                      "arch": info.get("Architecture"), "vm_cpus": info.get("NCPU"),
                      "vm_memory_bytes": info.get("MemTotal"), "server_version": info.get("ServerVersion"),
                      "storage_driver": info.get("Driver")},
        "docker_desktop_settings": settings,
        "load_generator": "runs on the macOS host (outside the container) and connects through Docker "
                          "Desktop's published port 127.0.0.1:<port>",
    }
