"""Every example script runs to completion and prints something (issue #26)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))


def test_there_are_examples() -> None:
    assert EXAMPLES


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda path: path.name)
def test_example_runs(script: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip()
