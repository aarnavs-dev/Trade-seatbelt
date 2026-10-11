"""Named log sources. The client picks a name, never a path."""

from pathlib import Path

NAMES = ("demo", "recon")


class Sources:
    """demo  = the log of the latest POST /demo/run, else out/demo/gate.log (what `make demo` writes) if present.
    recon = the bundled reconciliation fixture's log (read-only; every endpoint only ever reads it)."""

    def __init__(self, root: Path, recon_log: Path | None = None, default_demo_log: Path | None = None):
        self.root = root
        self.recon_log = recon_log or root / "fixtures" / "recon" / "gate_log.jsonl"
        self.default_demo_log = default_demo_log or root / "out" / "demo" / "gate.log"
        self.demo_log: Path | None = None  # set by the demo runner

    def resolve(self, name: str) -> Path:
        if name == "recon":
            return self.recon_log
        if name == "demo":
            return self.demo_log or self.default_demo_log
        raise KeyError(name)
