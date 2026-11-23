"""Tests for scripts/check_release.py, the release tag and dist file name check."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_release.py"


def sdist(version: str) -> str:
    return f"dist/openpickleball_engine-{version}.tar.gz"


def wheel(version: str) -> str:
    return f"dist/openpickleball_engine-{version}-py3-none-any.whl"


@pytest.fixture
def check_release() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_release", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("tag", "paths"),
    [
        pytest.param("v0.2.0", [sdist("0.2.0"), wheel("0.2.0")], id="release"),
        pytest.param("v0.3.0.dev1", [sdist("0.3.0.dev1"), wheel("0.3.0.dev1")], id="dev"),
        pytest.param("v1.0.0rc2", [sdist("1.0.0rc2"), wheel("1.0.0rc2")], id="rc"),
        pytest.param("v0.2.0", [wheel("0.2.0"), sdist("0.2.0")], id="wheel-first"),
    ],
)
def test_matching_tag_and_files_pass(
    check_release: ModuleType,
    capsys: pytest.CaptureFixture[str],
    tag: str,
    paths: list[str],
) -> None:
    assert check_release.main([tag, *paths]) == 0
    captured = capsys.readouterr()
    assert "ok" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    ("tag", "paths", "message"),
    [
        pytest.param(
            "0.2.0",
            [sdist("0.2.0"), wheel("0.2.0")],
            r"tag '0\.2\.0' does not start with 'v'",
            id="no-v",
        ),
        pytest.param(
            "v0.2.1",
            [sdist("0.2.0"), wheel("0.2.0")],
            r"tag 'v0\.2\.1' does not match version '0\.2\.0'",
            id="version-differs",
        ),
        pytest.param(
            "v0.3.0-dev1",
            [sdist("0.3.0.dev1"), wheel("0.3.0.dev1")],
            r"tag 'v0\.3\.0-dev1' does not match version '0\.3\.0\.dev1'",
            id="non-canonical",
        ),
        pytest.param(
            "v1.0.0-rc2",
            [sdist("1.0.0rc2"), wheel("1.0.0rc2")],
            r"tag 'v1\.0\.0-rc2' does not match version '1\.0\.0rc2'",
            id="non-canonical-rc",
        ),
        pytest.param(
            "v0.2.0",
            [wheel("0.2.0")],
            r"expected exactly one sdist, found 0",
            id="missing-sdist",
        ),
        pytest.param(
            "v0.2.0",
            [sdist("0.2.0")],
            r"expected exactly one wheel, found 0",
            id="missing-wheel",
        ),
        pytest.param(
            "v0.2.0",
            [sdist("0.2.0"), wheel("0.2.0"), wheel("0.2.0.post1")],
            r"expected exactly one wheel, found 2",
            id="two-wheels",
        ),
        pytest.param(
            "v0.2.0",
            [sdist("0.2.0"), wheel("0.2.0"), "dist/notes.txt"],
            r"unexpected file: notes\.txt",
            id="extra-file",
        ),
        pytest.param(
            "v0.2.0",
            [sdist("0.2.0"), wheel("0.2.1")],
            r"sdist version '0\.2\.0' differs from wheel version '0\.2\.1'",
            id="versions-differ",
        ),
        pytest.param(
            "v0.2.0",
            [sdist("0.2.0"), "dist/openpickleball_engine-0.2.0-cp312-cp312-linux_x86_64.whl"],
            r"unexpected file: openpickleball_engine-0\.2\.0-cp312-cp312-linux_x86_64\.whl",
            id="platform-wheel",
        ),
    ],
)
def test_problems_fail_and_are_named(
    check_release: ModuleType,
    capsys: pytest.CaptureFixture[str],
    tag: str,
    paths: list[str],
    message: str,
) -> None:
    assert check_release.main([tag, *paths]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert re.search(message, captured.err)


def test_no_dist_paths_fails(check_release: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_release.main(["v0.2.0"]) == 1
    assert "no dist files given" in capsys.readouterr().err


def test_no_arguments_fails(check_release: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert check_release.main([]) == 1
    assert "usage: check_release.py" in capsys.readouterr().err


def test_every_problem_is_reported(
    check_release: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_release.main(["v9.9.9", sdist("0.2.0"), "dist/extra.zip"]) == 1
    err = capsys.readouterr().err
    assert "expected exactly one wheel, found 0" in err
    assert "unexpected file: extra.zip" in err
    assert "tag 'v9.9.9' does not match version '0.2.0'" in err
