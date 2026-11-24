"""Every example script runs to completion and prints something (issue #26)."""

from __future__ import annotations

import ast
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


def _call_name(node: ast.Call) -> str:
    """Return the dotted name a call targets, such as 'Config.published', or ''."""
    parts: list[str] = []
    func: ast.expr = node.func
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return ""
    parts.append(func.id)
    return ".".join(reversed(parts))


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda path: path.name)
def test_example_loads_v1(script: Path) -> None:
    # Examples load the published set v1, never the Config() placeholders (issue #173).
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    published = [
        call
        for call in calls
        if _call_name(call) == "Config.published"
        and len(call.args) == 1
        and not call.keywords
        and isinstance(call.args[0], ast.Constant)
        and call.args[0].value == "v1"
    ]
    assert published, "no Config.published('v1') call"
    bare = [
        _call_name(call)
        for call in calls
        if _call_name(call) in ("Config", "RatingEngine") and not call.args and not call.keywords
    ]
    assert bare == []


V1 = 'from open_engine import Config, RatingEngine\ncfg = Config.published("v1")\n'


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(V1 + "engine = RatingEngine()\n", id="stray-RatingEngine"),
        pytest.param(V1 + "placeholder = Config()\n", id="stray-Config"),
        pytest.param(
            "from open_engine import Config, RatingEngine\nengine = RatingEngine(Config())\n",
            id="no-published-v1",
        ),
    ],
)
def test_example_check_catches_placeholder_configs(tmp_path: Path, source: str) -> None:
    # Guards the guard: each of these scripts must fail test_example_loads_v1.
    script = tmp_path / "example.py"
    script.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        test_example_loads_v1(script)


def test_example_check_accepts_published_v1(tmp_path: Path) -> None:
    script = tmp_path / "example.py"
    script.write_text(
        'from open_engine import Config, RatingEngine\nRatingEngine(Config.published("v1"))\n',
        encoding="utf-8",
    )
    test_example_loads_v1(script)
