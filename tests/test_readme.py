"""Tests that the README quick start runs unchanged against the package."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import open_engine

README = Path(__file__).parents[1] / "README.md"
INSTALL = re.compile(
    r"^pip install -i https://test\.pypi\.org/simple/ openpickleball-engine==(\S+)$", re.MULTILINE
)


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


def run_block(code: str) -> None:
    exec(compile(code, "README.md quick start", "exec"), {"__name__": "__readme__"})


def test_quick_start_runs(capsys: pytest.CaptureFixture[str]) -> None:
    run_block(quick_start_block(README))
    out = capsys.readouterr().out
    assert "alice" in out


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


def install_pins(readme: Path) -> list[str]:
    """Return every version pinned by a TestPyPI install line in the README."""
    return INSTALL.findall(readme.read_text(encoding="utf-8"))


def test_install_line_pins_package_version() -> None:
    # _version.py is the one place the version is set; the README must follow it.
    assert install_pins(README) == [open_engine.__version__]


def test_stale_install_pin_is_caught(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "## Install\n\n```bash\n"
        "pip install -i https://test.pypi.org/simple/ openpickleball-engine==0.0.1\n```\n",
        "utf-8",
    )
    assert install_pins(readme) == ["0.0.1"]
    assert install_pins(readme) != [open_engine.__version__]
