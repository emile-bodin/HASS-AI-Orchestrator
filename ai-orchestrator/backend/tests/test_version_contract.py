import json
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
EXPECTED = "0.13.7"


def test_release_version_is_consistent_across_addon_and_dashboard():
    addon = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
    package = json.loads((REPO / "dashboard" / "package.json").read_text(encoding="utf-8"))
    lockfile = json.loads((REPO / "dashboard" / "package-lock.json").read_text(encoding="utf-8"))
    changelog = (REPO.parent / "CHANGELOG.md").read_text(encoding="utf-8")

    assert addon["version"] == EXPECTED
    assert package["version"] == EXPECTED
    assert lockfile["version"] == EXPECTED
    assert lockfile["packages"][""]["version"] == EXPECTED
    assert re.search(rf"^## \[{re.escape(EXPECTED)}\]", changelog, re.MULTILINE)
