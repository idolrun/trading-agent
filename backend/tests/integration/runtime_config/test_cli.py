"""CLI in a fresh interpreter: proves it works with only its own imports (all models registered)."""

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.integration.conftest import _url


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"DATABASE_URL": _url("test_database_url")}
    return subprocess.run(  # noqa: S603 — fixed argv
        [sys.executable, "-m", "app.runtime_config.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_set_then_show(owner_engine: object) -> None:
    result = cli("set", "--file", "config.example.json", "--reason", "bootstrap")
    assert result.returncode == 0, result.stderr
    shown = json.loads(cli("show").stdout)
    assert shown["version"] == 1
    assert "entry_chase_pct" in shown["missing_for_entries"]


def test_invalid_file_rejected(owner_engine: object, tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"entry_chase_pct": "0"}))
    result = cli("set", "--file", str(bad), "--reason", "x")
    assert result.returncode == 2
    assert "entry_chase_pct" in result.stderr
