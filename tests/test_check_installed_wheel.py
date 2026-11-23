"""Tests for scripts/check_installed_wheel.py, the installed wheel load check."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

import open_engine
import open_engine._config as config_module

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_installed_wheel.py"


@pytest.fixture
def check_installed_wheel() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_installed_wheel", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_package_outside_the_checkout_passes(
    check_installed_wheel: ModuleType, tmp_path: Path
) -> None:
    assert check_installed_wheel.problems(tmp_path) == []


def test_a_package_imported_from_the_checkout_fails(check_installed_wheel: ModuleType) -> None:
    # The directory that holds the imported package stands in for a checkout,
    # so the test holds for an editable install and for a regular one.
    found = check_installed_wheel.problems(Path(open_engine.__file__).resolve().parents[1])
    assert len(found) == 1
    assert found[0].startswith("open_engine imported from the checkout")


def test_a_missing_packaged_file_fails(
    check_installed_wheel: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(label: str) -> str:
        raise FileNotFoundError(label)

    monkeypatch.setattr(config_module, "_packaged_parameter_set_text", missing)
    found = check_installed_wheel.problems(tmp_path)
    assert len(found) == 1
    assert found[0].startswith("published('v1') failed: Parameter set v1 could not be read")


def test_a_wrong_v1_fingerprint_fails(
    check_installed_wheel: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_installed_wheel, "V1_FINGERPRINT", "0" * 16)
    found = check_installed_wheel.problems(tmp_path)
    assert found == ["published('v1') has parameter fingerprint f9149f3c38a01efc, not " + "0" * 16]


def test_a_missing_v1_label_fails(
    check_installed_wheel: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, "_PUBLISHED_PARAMETER_SETS", ())
    found = check_installed_wheel.problems(tmp_path)
    assert found[0] == "published_sets() does not list v1: ()"
    assert found[1].startswith("published('v1') failed:")
