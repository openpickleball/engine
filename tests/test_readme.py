"""Tests that the README quick start runs unchanged against the package."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import open_engine
from open_engine import Config

README = Path(__file__).parents[1] / "README.md"
INSTALL_LINE = "pip install openpickleball-engine"
PIP = re.compile(r"^(?:python3? -m )?pip install ")
PIN = re.compile(r"openpickleball-engine==([^\s`]+)")
REPLAY_CALL = re.compile(r"\breplay\(([^)]*)\)")


def quick_start_block(readme: Path) -> str:
    """Return the first python code block under the README's '## Quick start'."""
    text = readme.read_text(encoding="utf-8")
    _, found, section = text.partition("\n## Quick start\n")
    assert found, "README has no '## Quick start' section"
    section = section.split("\n## ", 1)[0]
    _, found, rest = section.partition("```python\n")
    assert found, "'## Quick start' has no python code block"
    code, found, _ = rest.partition("```")
    assert found, "the quick start python code block is not closed"
    return code


def run_block(code: str) -> dict[str, Any]:
    """Run a code block and return the namespace it ran in."""
    namespace: dict[str, Any] = {"__name__": "__readme__"}
    exec(compile(code, "README.md quick start", "exec"), namespace)
    return namespace


def test_quick_start_runs(capsys: pytest.CaptureFixture[str]) -> None:
    run_block(quick_start_block(README))
    out = capsys.readouterr().out
    assert "alice" in out


def test_quick_start_uses_v1() -> None:
    # The quick start loads the published set v1, not the Config() placeholders (issue #173).
    namespace = run_block(quick_start_block(README))
    assert namespace["engine"].cfg == Config.published("v1")


def quick_start_section(readme: Path) -> str:
    """Return the README's '## Quick start' section, code blocks and prose together."""
    text = readme.read_text(encoding="utf-8")
    _, found, section = text.partition("\n## Quick start\n")
    assert found, "README has no '## Quick start' section"
    return section.split("\n## ", 1)[0]


def test_quick_start_replay_passes_cfg() -> None:
    # A replay pointer without cfg= runs on the Config() placeholders, not v1 (issue #173).
    calls = REPLAY_CALL.findall(quick_start_section(README))
    assert calls, "the quick start names no replay( call"
    assert [args for args in calls if "cfg=" not in args] == []


def test_broken_quick_start_raises(tmp_path: Path) -> None:
    # Guards the guard: a block that no longer matches the API must fail the test.
    text = README.read_text(encoding="utf-8")
    broken = tmp_path / "README.md"
    broken.write_text(text.replace("engine.record(", "engine.record_missing(", 1), "utf-8")
    with pytest.raises(AttributeError, match="record_missing"):
        run_block(quick_start_block(broken))


def test_missing_quick_start_section_fails(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("# Title\n\n## Setup\n\n```python\nprint(1)\n```\n", "utf-8")
    with pytest.raises(AssertionError, match="no '## Quick start' section"):
        quick_start_block(readme)


def install_lines(readme: Path) -> list[str]:
    """Return every README line that installs the package with pip."""
    text = readme.read_text(encoding="utf-8")
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if PIP.search(line) and "openpickleball-engine" in line]


def install_pins(readme: Path) -> list[str]:
    """Return every version the README pins with openpickleball-engine==<version>."""
    return PIN.findall(readme.read_text(encoding="utf-8"))


def test_install_line_is_the_plain_pypi_install() -> None:
    # A final release installs from PyPI with no index, --pre, or pin.
    assert install_lines(README) == [INSTALL_LINE]


def test_install_line_pins_package_version() -> None:
    # _version.py is the one place the version is set; the README pin must follow it.
    assert install_pins(README) == [open_engine.__version__]


def test_stale_install_lines_are_caught(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "## Install\n\n```bash\n"
        "pip install -i https://test.pypi.org/simple/ openpickleball-engine==0.0.1\n```\n",
        "utf-8",
    )
    assert install_lines(readme) != [INSTALL_LINE]
    assert install_pins(readme) == ["0.0.1"]
    assert install_pins(readme) != [open_engine.__version__]
