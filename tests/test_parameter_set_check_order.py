"""Config.from_parameter_set checks in its documented order (issue #48).

Each test gives an input that fails two adjacent checks and asserts the
earlier one is raised. The order is the one its docstring, docs/behavior.md
("Config validation"), and docs/parameters.md state. All ids are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_engine import Config, ConfigError

ROOT = Path(__file__).resolve().parents[1]
FP_FORM = "parameter_fingerprint must be exactly 16 lowercase hex characters"
LABEL_FORM = "params_version must be v<N>"
UNKNOWN = "is not a parameter set this release publishes"


def _v1() -> dict[str, object]:
    data = json.loads((ROOT / "params" / "v1.json").read_text(encoding="utf-8"))
    assert type(data) is dict
    return data


def _with_params(data: dict[str, object], **changes: object) -> dict[str, object]:
    params = data["parameters"]
    assert type(params) is dict
    return {**data, "parameters": {**params, **changes}}


def test_label_type_comes_before_fingerprint_type() -> None:
    data = {**_v1(), "params_version": None, "parameter_fingerprint": None}
    with pytest.raises(ConfigError, match="params_version must be exactly a str"):
        Config.from_parameter_set(data)


def test_smuggled_label_comes_before_fingerprint_form() -> None:
    data = _with_params({**_v1(), "parameter_fingerprint": "x"}, version="x")
    with pytest.raises(ConfigError, match="must not hold version"):
        Config.from_parameter_set(data)


def test_fingerprint_form_comes_before_label_form() -> None:
    data = {**_v1(), "parameter_fingerprint": "x", "params_version": "v01"}
    with pytest.raises(ConfigError, match=FP_FORM):
        Config.from_parameter_set(data)


def test_fingerprint_form_comes_before_an_unknown_label() -> None:
    data = {**_v1(), "parameter_fingerprint": "X" * 16, "params_version": "v2"}
    with pytest.raises(ConfigError, match=FP_FORM):
        Config.from_parameter_set(data)


@pytest.mark.parametrize("label", ["v01", "v0", "1", "V1", "v1\n", ""])
def test_label_form_comes_before_the_published_check(label: str) -> None:
    data = {**_v1(), "params_version": label}
    with pytest.raises(ConfigError, match=LABEL_FORM):
        Config.from_parameter_set(data)


def test_unknown_label_comes_before_the_published_fingerprint() -> None:
    data = {**_v1(), "params_version": "v2", "parameter_fingerprint": "0" * 16}
    with pytest.raises(ConfigError, match=UNKNOWN):
        Config.from_parameter_set(data)


def test_published_fingerprint_comes_before_the_values() -> None:
    data = _with_params({**_v1(), "parameter_fingerprint": "0" * 16}, k_min=-1.0)
    with pytest.raises(ConfigError, match="published"):
        Config.from_parameter_set(data)


def test_bad_label_comes_before_a_bad_value() -> None:
    data = _with_params({**_v1(), "params_version": "v01"}, k_min=-1.0)
    with pytest.raises(ConfigError, match=LABEL_FORM):
        Config.from_parameter_set(data)


def test_unknown_label_comes_before_a_bad_value() -> None:
    data = _with_params({**_v1(), "params_version": "v2"}, k_min=-1.0)
    with pytest.raises(ConfigError, match=UNKNOWN):
        Config.from_parameter_set(data)


def test_values_come_before_the_computed_fingerprint() -> None:
    data = _with_params(_v1(), k_min=-1.0)
    with pytest.raises(ConfigError, match="K-factor bounds are invalid"):
        Config.from_parameter_set(data)


def test_missing_field_comes_before_the_computed_fingerprint() -> None:
    data = _v1()
    params = data["parameters"]
    assert type(params) is dict
    del params["k_min"]
    with pytest.raises(ConfigError, match="missing keys: k_min"):
        Config.from_parameter_set(data)
