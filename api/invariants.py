"""Invariants with test status. The list comes from docs/INVARIANTS.md; the status comes only from a pytest JUnit XML
result file (written by `make test`). With no result file, every status is "unknown": nothing is assumed to pass."""

import datetime as dt
import re
import xml.etree.ElementTree as ET
from pathlib import Path

_REF = re.compile(r"^(tests/\w+\.py)(?:::([\w\[\],.\-*]+))?$")
_NAME = re.compile(r"^::([\w\[\],.\-*]+)$")


def _short(claim: str) -> str:
    t = re.sub(r"\*\*|`", "", claim).strip()
    m = re.search(r"\.(\s|$)", t)
    t = t[: m.start() + 1] if m else t
    return t if len(t) <= 200 else t[:200].rsplit(" ", 1)[0] + "…"


def parse_invariants(md: Path) -> list[dict]:
    rows = []
    for line in md.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) < 2 or not re.fullmatch(r"\d+[a-z]?", cells[0]):
            continue
        refs, last = [], None
        for span in re.findall(r"`([^`]+)`", " ".join(cells[1:])):  # row 6 keeps its tests in the claim cell
            if m := _REF.match(span):
                last = m.group(1)
                refs.append({"file": last, "name": m.group(2)})
            elif (m := _NAME.match(span)) and last:
                refs.append({"file": last, "name": m.group(1)})
        rows.append({"id": cells[0], "claim": _short(cells[1]), "refs": refs})
    return rows


def read_junit(path: Path) -> dict | None:
    """{module: [(test name, 'passed'|'failed'|'skipped')]} plus suite totals, or None if there is no file."""
    if not path.exists():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    mods: dict[str, list] = {}
    for tc in root.iter("testcase"):
        st = "failed" if tc.find("failure") is not None or tc.find("error") is not None else (
            "skipped" if tc.find("skipped") is not None else "passed")
        mods.setdefault(tc.get("classname", ""), []).append((tc.get("name", ""), st))
    tot = {k: sum(int(s.get(k, 0)) for s in suites) for k in ("tests", "failures", "errors", "skipped")}
    return {"mods": mods, "totals": tot,
            "generated": dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc).isoformat(timespec="seconds")}


def build(md: Path, junit: Path, root: Path) -> dict:
    inv, res = parse_invariants(md), read_junit(junit)
    out = []
    for row in inv:
        if not row["refs"]:
            out.append({**row, "status": "not_pytest", "passed": 0, "failed": 0, "skipped": 0, "unmatched": 0})
            continue
        if res is None:
            out.append({**row, "status": "unknown", "passed": 0, "failed": 0, "skipped": 0, "unmatched": len(row["refs"])})
            continue
        n = {"passed": 0, "failed": 0, "skipped": 0}
        unmatched = 0
        for ref in row["refs"]:
            mod = ref["file"][:-3].replace("/", ".")
            want = ref["name"]
            hits = [st for name, st in res["mods"].get(mod, [])
                    if want is None or name == want or name.startswith(want + "[")
                    or (want.endswith("*") and name.startswith(want[:-1]))]
            if not hits:
                unmatched += 1
            for st in hits:
                n[st] += 1
        status = ("failing" if n["failed"] else "unknown" if n["passed"] == 0 else "partial" if unmatched else "passing")
        out.append({**row, "status": status, **n, "unmatched": unmatched})
    return {"source": str(junit.relative_to(root)) if junit.is_relative_to(root) else str(junit),
            "result_file_found": res is not None, "generated": res["generated"] if res else None,
            "suite": res["totals"] if res else None, "invariants": out,
            "note": None if res else "No test result file yet. Run `make test` to write one."}
