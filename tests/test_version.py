"""Release metadata stays synchronized across the Python and TypeScript packages."""
import json
import re
from pathlib import Path

import jev_decision
from jev_decision import mcp

ROOT = Path(__file__).resolve().parents[1]


def test_single_python_version_source_matches_typescript_package():
    version = jev_decision.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?", version)
    assert mcp.SERVER_VERSION == version
    package = json.loads((ROOT / "ts/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "ts/package-lock.json").read_text(encoding="utf-8"))
    assert package["version"] == lock["version"] == lock["packages"][""]["version"] == version
    source = (ROOT / "ts/src/index.ts").read_text(encoding="utf-8")
    assert '"User-Agent": "jev-decision-ts/' + version + '"' in source
