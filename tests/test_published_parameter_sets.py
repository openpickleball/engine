"""The packaged parameter sets and Config.published (issue #165).

The wheel ships every published set under open_engine/params/, and
Config.published(label) loads one through Config.from_parameter_set, so it
gets the same label and fingerprint checks. All ids are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import open_engine._config as config_module
from open_engine import Config, ConfigError, __version__
from open_engine._config import _PUBLISHED_PARAMETER_SETS

PARAMS_DIR = Path(__file__).resolve().parents[1] / "src" / "open_engine" / "params"
V1_FINGERPRINT = "f9149f3c38a01efc"
UNPUBLISHED = "is not a parameter set this release publishes"


def test_v1_loads_from_the_package() -> None:
    cfg = Config.published("v1")
    assert cfg.parameter_fingerprint() == V1_FINGERPRINT
    assert (cfg.params_version, cfg.version) == ("v1", __version__)


def test_published_equals_from_parameter_set_of_the_file() -> None:
    data = json.loads((PARAMS_DIR / "v1.json").read_text(encoding="utf-8"))
    assert Config.published("v1") == Config.from_parameter_set(data)


def test_published_sets_lists_the_table_labels_in_order() -> None:
    assert Config.published_sets() == tuple(label for label, _ in _PUBLISHED_PARAMETER_SETS)
    assert Config.published_sets() == ("v1",)


def test_published_sets_matches_the_packaged_files() -> None:
    files = sorted(path.stem for path in PARAMS_DIR.glob("*.json"))
    assert sorted(Config.published_sets()) == files


def test_every_published_label_loads_with_its_table_fingerprint() -> None:
    table = dict(_PUBLISHED_PARAMETER_SETS)
    for label in Config.published_sets():
        cfg = Config.published(label)
        assert cfg.params_version == label
        assert cfg.parameter_fingerprint() == table[label]


@pytest.mark.parametrize("label", ["v2", "v0", "v01", "V1", "", "v1 ", "../v1", "x" * 50_000])
def test_an_unpublished_label_is_rejected_without_echoing_it(label: str) -> None:
    with pytest.raises(ConfigError, match=UNPUBLISHED) as info:
        Config.published(label)
    message = str(info.value)
    assert "(it publishes v1)" in message
    assert len(message) < 200


def test_unknown_label_error_type_matches_from_parameter_set() -> None:
    data = json.loads((PARAMS_DIR / "v1.json").read_text(encoding="utf-8"))
    with pytest.raises(ConfigError, match=UNPUBLISHED) as loader:
        Config.from_parameter_set({**data, "params_version": "v2"})
    with pytest.raises(ConfigError, match=UNPUBLISHED) as packaged:
        Config.published("v2")
    assert type(packaged.value) is type(loader.value)


class _Label(str):
    pass


@pytest.mark.parametrize("label", [None, 1, b"v1", ["v1"], _Label("v1")])
def test_a_label_not_exactly_a_str_is_rejected(label: object) -> None:
    with pytest.raises(ConfigError, match="label must be exactly a str, not"):
        Config.published(label)  # type: ignore[arg-type]


def test_a_published_label_with_no_packaged_file_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config_module,
        "_PUBLISHED_PARAMETER_SETS",
        (*_PUBLISHED_PARAMETER_SETS, ("v999", "0123456789abcdef")),
    )
    with pytest.raises(ConfigError, match="Parameter set v999 could not be read"):
        Config.published("v999")


@pytest.mark.parametrize("text", ["{", "[" * 100_000, "\ud800"])
def test_a_packaged_file_that_is_not_json_raises_config_error(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, "_packaged_parameter_set_text", lambda label: text)
    with pytest.raises(ConfigError, match="Parameter set v1 could not be read"):
        Config.published("v1")


def test_a_packaged_file_goes_through_the_loader_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = json.loads((PARAMS_DIR / "v1.json").read_text(encoding="utf-8"))
    data["parameter_fingerprint"] = "0" * 16
    monkeypatch.setattr(
        config_module, "_packaged_parameter_set_text", lambda label: json.dumps(data)
    )
    with pytest.raises(ConfigError, match="differs from the published"):
        Config.published("v1")
