"""Tests for scripts/check_sdist.py, the source distribution content check."""

from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_sdist.py"
ROOT = "openpickleball_engine-0.0.0"
ALLOWED = [
    "src/open_engine/__init__.py",
    "src/open_engine/py.typed",
    "tests/test_engine.py",
    "examples/basic_match.py",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "pyproject.toml",
    "PKG-INFO",
    ".gitignore",
]


@pytest.fixture
def check_sdist() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_sdist", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_sdist(directory: Path, names: list[str]) -> Path:
    path = directory / f"{ROOT}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name in names:
            data = b"synthetic"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def rooted(paths: list[str]) -> list[str]:
    return [f"{ROOT}/{path}" for path in paths]


def test_allowed_set_passes(
    check_sdist: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = make_sdist(tmp_path, rooted(ALLOWED))
    assert check_sdist.main([str(path)]) == 0
    captured = capsys.readouterr()
    assert str(path) in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    ("extra", "named"),
    [
        ("uv.lock", "uv.lock"),
        (".superpowers/review.diff", ".superpowers"),
        ("CLAUDE.md", "CLAUDE.md"),
        ("docs/plans/x.md", "docs"),
    ],
)
def test_unexpected_top_level_entry_fails(
    check_sdist: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: str,
    named: str,
) -> None:
    path = make_sdist(tmp_path, rooted([*ALLOWED, extra]))
    assert check_sdist.main([str(path)]) == 1
    assert named in capsys.readouterr().err


def test_member_outside_root_fails(
    check_sdist: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = make_sdist(tmp_path, [*rooted(ALLOWED), "stray.txt"])
    assert check_sdist.main([str(path)]) == 1
    assert "stray.txt" in capsys.readouterr().err


def test_missing_required_entry_fails(
    check_sdist: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names = [name for name in ALLOWED if name != "PKG-INFO"]
    path = make_sdist(tmp_path, rooted(names))
    assert check_sdist.main([str(path)]) == 1
    assert "PKG-INFO" in capsys.readouterr().err


def test_no_arguments_fails(check_sdist: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_sdist.main([]) == 1
    assert capsys.readouterr().err != ""


def test_problems_is_empty_for_clean_names(check_sdist: ModuleType) -> None:
    assert check_sdist.problems(rooted(ALLOWED)) == []
