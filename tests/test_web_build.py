"""Build check for the web app (type-check + production build, including the /app/follow page). Skipped without Node."""

import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"


@pytest.mark.skipif(shutil.which("npm") is None or not (WEB / "node_modules").is_dir(), reason="needs Node and `npm ci` in web/")
def test_web_type_checks_and_builds():
    r = subprocess.run(["npm", "run", "build"], cwd=WEB, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert any((WEB / "dist" / "assets").glob("Follow-*.js")), "the follow page should be its own chunk"
