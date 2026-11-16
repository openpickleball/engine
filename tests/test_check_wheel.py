"""Tests for scripts/check_wheel.py, the wheel content check."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_wheel.py"
DIST_INFO = "openpickleball_engine-0.0.0.dist-info"
PACKAGE = [
    "open_engine/__init__.py",
    "open_engine/_errors.py",
    "open_engine/_version.py",
    "open_engine/engine.py",
    "open_engine/winprob.py",
    "open_engine/py.typed",
]
METADATA = [
    f"{DIST_INFO}/METADATA",
    f"{DIST_INFO}/WHEEL",
    f"{DIST_INFO}/licenses/LICENSE",
    f"{DIST_INFO}/RECORD",
]
ALLOWED = [*PACKAGE, *METADATA]


@pytest.fixture
def check_wheel() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_wheel", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_wheel(directory: Path, names: list[str]) -> Path:
    path = directory / "openpickleball_engine-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        for name in names:
            wheel.writestr(name, "synthetic")
    return path


def test_allowed_set_passes(
    check_wheel: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = make_wheel(tmp_path, ALLOWED)
    assert check_wheel.main([str(path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"{path}: ok\n"
    assert captured.err == ""


def test_problems_is_empty_for_clean_names(check_wheel: ModuleType) -> None:
    assert check_wheel.problems(ALLOWED) == []


@pytest.mark.parametrize(
    "extra",
    [
        "open_engine/scratch.py",
        "open_engine/sub/engine.py",
        "open_engine/__pycache__/engine.cpython-312.pyc",
        "tests/test_engine.py",
        "CLAUDE.md",
        "open_engine.py",
        "other_package-1.0.dist-info/METADATA",
        f"{DIST_INFO}/../stray.txt",
        f"{DIST_INFO}//RECORD",
        "open_engine/./engine.py",
    ],
)
def test_unexpected_member_fails(
    check_wheel: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: str,
) -> None:
    path = make_wheel(tmp_path, [*ALLOWED, extra])
    assert check_wheel.main([str(path)]) == 1
    err = capsys.readouterr().err
    assert f"{path}: unexpected member: {extra}" in err


@pytest.mark.parametrize("missing", PACKAGE)
def test_missing_package_file_fails(
    check_wheel: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    missing: str,
) -> None:
    path = make_wheel(tmp_path, [name for name in ALLOWED if name != missing])
    assert check_wheel.main([str(path)]) == 1
    assert f"missing required member: {missing}" in capsys.readouterr().err


def test_missing_dist_info_fails(check_wheel: ModuleType) -> None:
    assert check_wheel.problems(PACKAGE) == [
        "no dist-info directory starting with 'openpickleball_engine-'"
    ]


def test_more_than_one_dist_info_fails(check_wheel: ModuleType) -> None:
    other = "openpickleball_engine-9.9.9.dist-info"
    found = check_wheel.problems([*ALLOWED, f"{other}/METADATA"])
    assert found == [f"more than one dist-info directory: {DIST_INFO}, {other}"]


def test_directory_entries_are_allowed(check_wheel: ModuleType) -> None:
    names = ["open_engine/", f"{DIST_INFO}/", *ALLOWED]
    assert check_wheel.problems(names) == []


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        pytest.param(
            [*ALLOWED, "open_engine/__pycache__/"],
            "unexpected member: open_engine/__pycache__/",
            id="package-subdirectory-entry",
        ),
        pytest.param(
            [*ALLOWED, "open_engine/sub/pkg/"],
            "unexpected member: open_engine/sub/pkg/",
            id="nested-package-directory-entry",
        ),
        pytest.param(
            [*ALLOWED, f"{DIST_INFO}/licenses/"],
            f"unexpected member: {DIST_INFO}/licenses/",
            id="dist-info-subdirectory-entry",
        ),
        pytest.param(
            [*PACKAGE, f"{DIST_INFO}/"],
            f"missing required member: {DIST_INFO}/METADATA",
            id="dist-info-directory-entry-only",
        ),
        pytest.param(
            [name for name in ALLOWED if name != f"{DIST_INFO}/WHEEL"],
            f"missing required member: {DIST_INFO}/WHEEL",
            id="dist-info-without-wheel",
        ),
        pytest.param(
            [name for name in ALLOWED if name != f"{DIST_INFO}/RECORD"],
            f"missing required member: {DIST_INFO}/RECORD",
            id="dist-info-without-record",
        ),
        pytest.param(
            [
                *PACKAGE,
                "openpickleball_engine-.dist-info/METADATA",
                "openpickleball_engine-.dist-info/WHEEL",
                "openpickleball_engine-.dist-info/RECORD",
            ],
            "no dist-info directory starting with 'openpickleball_engine-'",
            id="dist-info-empty-version",
        ),
        pytest.param(
            [*ALLOWED, "open_engine/engine.py"],
            "duplicate member: open_engine/engine.py",
            id="duplicate-member",
        ),
    ],
)
def test_invalid_layout_is_reported(
    check_wheel: ModuleType, names: list[str], expected: str
) -> None:
    assert expected in check_wheel.problems(names)


def test_problems_are_sorted_and_all_named(check_wheel: ModuleType) -> None:
    names = [name for name in ALLOWED if name != "open_engine/py.typed"]
    found = check_wheel.problems([*names, "z.txt", "a.txt"])
    assert found == [
        "unexpected member: a.txt",
        "unexpected member: z.txt",
        "missing required member: open_engine/py.typed",
    ]


def test_each_wheel_is_reported(
    check_wheel: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = make_wheel(tmp_path, ALLOWED)
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad = make_wheel(bad_dir, [*ALLOWED, "stray.txt"])
    assert check_wheel.main([str(good), str(bad)]) == 1
    captured = capsys.readouterr()
    assert captured.out == f"{good}: ok\n"
    assert captured.err == f"{bad}: unexpected member: stray.txt\n"


def test_no_arguments_fails(check_wheel: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_wheel.main([]) == 1
    assert "usage" in capsys.readouterr().err
