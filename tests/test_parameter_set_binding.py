"""A parameter set label is bound to the values this release publishes for it (issue #48).

The recorded fingerprint comes from the same file as the values, so a file
that agrees with itself proves nothing. Config.from_parameter_set must also
check the label and the recorded fingerprint against the sets the release
publishes. All ids are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_engine import Config, ConfigError, __version__

ROOT = Path(__file__).resolve().parents[1]
V1_FINGERPRINT = "f9149f3c38a01efc"


def _v1() -> dict[str, object]:
    data = json.loads(
        (ROOT / "src" / "open_engine" / "params" / "v1.json").read_text(encoding="utf-8")
    )
    assert type(data) is dict
    return data


def _forged(label: str) -> dict[str, object]:
    """A self-consistent set: other values, with their own true fingerprint."""
    cfg = Config(k_min=0.5, k_max=0.6)
    params = cfg.to_dict()
    del params["version"], params["params_version"]
    return {
        "params_version": label,
        "parameter_fingerprint": cfg.parameter_fingerprint(),
        "parameters": params,
    }


def test_a_self_consistent_forged_v1_is_rejected() -> None:
    data = _forged("v1")
    with pytest.raises(ConfigError, match="published") as info:
        Config.from_parameter_set(data)
    message = str(info.value)
    assert V1_FINGERPRINT in message
    assert str(data["parameter_fingerprint"]) in message


def test_the_v1_values_relabelled_as_v2_are_rejected() -> None:
    data = {**_v1(), "params_version": "v2"}
    with pytest.raises(ConfigError, match="is not a parameter set this release publishes"):
        Config.from_parameter_set(data)


def test_a_self_consistent_unknown_label_is_rejected() -> None:
    with pytest.raises(ConfigError, match="is not a parameter set this release publishes"):
        Config.from_parameter_set(_forged("v2"))


@pytest.mark.parametrize(
    "recorded",
    ["x" * 50_000, "F9149F3C38A01EFC", "f9149f3c38a01ef", "f9149f3c38a01efc0", "", " " * 16],
)
def test_a_malformed_fingerprint_is_rejected_without_echoing_it(recorded: str) -> None:
    data = {**_v1(), "parameter_fingerprint": recorded}
    with pytest.raises(ConfigError, match="16 lowercase hex characters") as info:
        Config.from_parameter_set(data)
    assert len(str(info.value)) < 200
    if recorded:
        assert recorded not in str(info.value)


def test_v1_still_loads() -> None:
    cfg = Config.from_parameter_set(_v1())
    assert (cfg.params_version, cfg.parameter_fingerprint()) == ("v1", V1_FINGERPRINT)
    assert cfg.version == __version__


def test_the_published_table_matches_the_params_files() -> None:
    from open_engine._config import _PUBLISHED_PARAMETER_SETS

    files = {}
    for path in sorted((ROOT / "src" / "open_engine" / "params").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert path.name == f"{data['params_version']}.json"
        files[data["params_version"]] = data["parameter_fingerprint"]
    assert dict(_PUBLISHED_PARAMETER_SETS) == files
