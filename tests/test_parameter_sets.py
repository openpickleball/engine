"""Published parameter sets and their loader (issue #48, the V1 part).

params/v<N>.json holds a named parameter set. Config.from_parameter_set
loads one and trusts its label only after the values hash to the recorded
parameter fingerprint. The files hold parameter values and aggregate counts
only, no match data. All player and match ids in this module are synthetic.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import fields
from datetime import date, timedelta
from pathlib import Path

import pytest

from open_engine import (
    Config,
    ConfigError,
    Match,
    MatchType,
    PlayerSummary,
    RatingEngine,
    RatingFormat,
    __version__,
    replay,
)

ROOT = Path(__file__).resolve().parents[1]
PARAMS_DIR = ROOT / "params"
V1_PATH = PARAMS_DIR / "v1.json"
START = date(2026, 3, 2)
LABELS = ("version", "params_version")
REQUIRED = ("params_version", "parameter_fingerprint", "parameters")
V1_FINGERPRINT = "f9149f3c38a01efc"
DEFAULT_FINGERPRINT = "1058623faf5d9367"
# The four values V1 changes, as the evaluation recorded them.
V1_VALUES: dict[str, object] = {
    "scale_by_scoring": {"side_out": 1.0, "rally": 1.0},
    "win_calibration_by_scoring": {"side_out": 0.476, "rally": 1.0},
    "k_min": 0.1079136690647482,
    "k_max": 0.539568345323741,
}


def _v1() -> dict[str, object]:
    data = json.loads(V1_PATH.read_text(encoding="utf-8"))
    assert type(data) is dict
    return data


def _parameters(data: dict[str, object]) -> dict[str, object]:
    params = data["parameters"]
    assert type(params) is dict
    return params


class _Dict(dict[str, object]):
    pass


# -- criterion 1: the file loads to the V1 config ------------------------------


def test_v1_loads_with_its_label_and_fingerprint() -> None:
    cfg = Config.from_parameter_set(_v1())
    assert cfg.params_version == "v1"
    assert cfg.parameter_fingerprint() == V1_FINGERPRINT
    assert cfg.version == __version__


def test_v1_holds_the_four_values_and_defaults_elsewhere() -> None:
    got = Config.from_parameter_set(_v1()).to_dict()
    default = Config().to_dict()
    for name in LABELS:
        del got[name], default[name]
    assert {name: got[name] for name in V1_VALUES} == V1_VALUES
    for name in V1_VALUES:
        assert type(got[name]) is type(V1_VALUES[name])
    rest = {name: value for name, value in got.items() if name not in V1_VALUES}
    assert rest == {name: value for name, value in default.items() if name not in V1_VALUES}
    assert all(got[name] != default[name] for name in V1_VALUES)


def test_v1_file_fields() -> None:
    data = _v1()
    assert data["params_version"] == "v1"
    assert data["parameter_fingerprint"] == V1_FINGERPRINT
    assert data["frozen_on"] == "2026-11-20"
    assert data["data"] == {
        "matches": 1601,
        "first_match_date": "2025-08-10",
        "last_match_date": "2026-11-20",
        "match_dates": 52,
        "players": 189,
        "doubles": 1601,
        "side_out": 1601,
        "rally": 0,
        "league": 1283,
        "tournament": 318,
        "fit_window_start": "2026-04-12",
        "data_version": "28bcab9de3af",
    }
    engine = data["fitted_on_engine"]
    assert type(engine) is dict
    assert engine["version"] == "0.3.0.dev0"
    assert engine["commit"] == "e62468c508126ed46b5a26a82f175ba17dd78371"
    assert engine["fingerprint"] == "22b53a37587d6881"
    assert engine["default_fingerprint"] == "17f2981186edf96c"
    assert type(engine["note"]) is str
    assert data["evidence"] == (
        "GeoBradDev/open-engine-evaluation, reports/v1-fit-2026-11/v1-fit.md, "
        "evaluation commit 995de996541608632b585c1d59d1089b2b6bc2c4"
    )


@pytest.mark.parametrize("path", sorted(PARAMS_DIR.glob("*.json")), ids=lambda p: p.name)
def test_every_params_file_is_sorted_two_space_json(path: Path) -> None:
    # A duplicate key or any other non-canonical form changes the text.
    text = path.read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"


# -- criterion 2: the file's parameters are the config's to_dict() --------------


def test_v1_parameters_equal_the_loaded_to_dict() -> None:
    data = _v1()
    got = Config.from_parameter_set(data).to_dict()
    for name in LABELS:
        del got[name]
    params = _parameters(data)
    assert list(sorted(params)) == list(sorted(got))
    for name, value in got.items():
        assert params[name] == value, name


# -- criterion 3: the file matches the evaluation's recorded fingerprint ---------


def test_v1_parameters_hash_to_the_evaluation_fingerprint() -> None:
    payload = {**_parameters(_v1()), "version": "0.3.0.dev0"}
    text = json.dumps(payload, sort_keys=True)
    assert hashlib.sha256(text.encode()).hexdigest()[:16] == "22b53a37587d6881"


def test_default_old_scheme_fingerprint() -> None:
    payload = Config(version="0.3.0.dev0").to_dict()
    del payload["params_version"]
    text = json.dumps(payload, sort_keys=True)
    assert hashlib.sha256(text.encode()).hexdigest()[:16] == "17f2981186edf96c"


# -- criterion 4: every rejection raises ConfigError ----------------------------


@pytest.mark.parametrize("data", [None, [], "v1", 1, _Dict()])
def test_rejects_data_that_is_not_exactly_a_dict(data: object) -> None:
    with pytest.raises(ConfigError, match="exactly a dict"):
        Config.from_parameter_set(data)  # type: ignore[arg-type]


def test_rejects_a_dict_subclass_holding_a_valid_set() -> None:
    data = _Dict(_v1())
    with pytest.raises(ConfigError, match="not _Dict"):
        Config.from_parameter_set(data)


@pytest.mark.parametrize("key", [1, None, ("params_version",)])
def test_rejects_a_key_that_is_not_a_str(key: object) -> None:
    data: dict[object, object] = {**_v1(), key: "x"}
    with pytest.raises(ConfigError, match="keys of type"):
        Config.from_parameter_set(data)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", REQUIRED)
def test_rejects_a_missing_required_key(name: str) -> None:
    data = _v1()
    del data[name]
    with pytest.raises(ConfigError, match=f"missing keys: {name}$"):
        Config.from_parameter_set(data)


def test_missing_keys_are_named_sorted() -> None:
    with pytest.raises(ConfigError, match=f"missing keys: {', '.join(sorted(REQUIRED))}$"):
        Config.from_parameter_set({})


@pytest.mark.parametrize("value", [None, 1, b"f9149f3c38a01efc", ["f9149f3c38a01efc"]])
def test_rejects_a_fingerprint_that_is_not_a_str(value: object) -> None:
    data = {**_v1(), "parameter_fingerprint": value}
    with pytest.raises(ConfigError, match="parameter_fingerprint must be exactly a str"):
        Config.from_parameter_set(data)


@pytest.mark.parametrize("value", [None, [], "parameters", 1])
def test_rejects_parameters_that_are_not_a_dict(value: object) -> None:
    data = {**_v1(), "parameters": value}
    with pytest.raises(ConfigError, match="parameters must be exactly a dict"):
        Config.from_parameter_set(data)


def test_rejects_parameters_that_are_a_dict_subclass() -> None:
    data = _v1()
    data["parameters"] = _Dict(_parameters(data))
    with pytest.raises(ConfigError, match="parameters must be exactly a dict, not _Dict"):
        Config.from_parameter_set(data)


def test_rejects_parameters_with_a_key_that_is_not_a_str() -> None:
    data = _v1()
    params: dict[object, object] = {**_parameters(data), 1: 1.0}
    data["parameters"] = params
    with pytest.raises(ConfigError, match="parameters has unknown keys: keys of type int"):
        Config.from_parameter_set(data)


@pytest.mark.parametrize(
    ("smuggled", "value"),
    [("version", __version__), ("params_version", "v1"), ("params_version", "v2")],
)
def test_rejects_a_label_inside_parameters(smuggled: str, value: str) -> None:
    data = _v1()
    data["parameters"] = {**_parameters(data), smuggled: value}
    with pytest.raises(ConfigError, match=f"parameters must not hold {smuggled}"):
        Config.from_parameter_set(data)


def test_rejects_parameters_holding_both_labels_naming_both() -> None:
    data = _v1()
    data["parameters"] = {**_parameters(data), "version": "x", "params_version": "v1"}
    with pytest.raises(ConfigError, match="must not hold params_version, version"):
        Config.from_parameter_set(data)


def test_rejects_parameters_missing_a_field() -> None:
    data = _v1()
    params = _parameters(data)
    del params["k_min"]
    with pytest.raises(ConfigError, match="missing keys: k_min"):
        Config.from_parameter_set(data)


def test_rejects_parameters_with_an_unknown_field() -> None:
    data = _v1()
    data["parameters"] = {**_parameters(data), "k_mid": 0.3}
    with pytest.raises(ConfigError, match="unknown keys: k_mid"):
        Config.from_parameter_set(data)


@pytest.mark.parametrize("label", ["v01", "1", "V1", "v1\n", "v0", ""])
def test_rejects_a_malformed_label(label: str) -> None:
    data = {**_v1(), "params_version": label}
    with pytest.raises(ConfigError, match="params_version must be v<N>"):
        Config.from_parameter_set(data)


@pytest.mark.parametrize("label", [1, None, b"v1"])
def test_rejects_a_label_that_is_not_a_str(label: object) -> None:
    data = {**_v1(), "params_version": label}
    with pytest.raises(ConfigError, match="params_version must be"):
        Config.from_parameter_set(data)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("k_min", 0.1079136690647482 + 1e-12),
        ("k_max", 0.6),
        ("win_calibration_by_scoring", {"side_out": 1.0, "rally": 1.0}),
        ("max_rating", 8),
    ],
)
def test_rejects_a_changed_value_under_the_recorded_fingerprint(name: str, value: object) -> None:
    data = _v1()
    _parameters(data)[name] = value
    computed = Config.from_dict(
        {**_parameters(data), "version": __version__, "params_version": "v1"}
    ).parameter_fingerprint()
    assert computed != V1_FINGERPRINT
    with pytest.raises(ConfigError, match="does not match its values") as info:
        Config.from_parameter_set(data)
    assert V1_FINGERPRINT in str(info.value)
    assert computed in str(info.value)
    assert "v1" in str(info.value)


@pytest.mark.parametrize("recorded", [DEFAULT_FINGERPRINT, "0" * 16])
def test_rejects_the_right_values_under_a_wrong_fingerprint(recorded: str) -> None:
    data = {**_v1(), "parameter_fingerprint": recorded}
    with pytest.raises(ConfigError, match="differs from the published") as info:
        Config.from_parameter_set(data)
    assert recorded in str(info.value)
    assert V1_FINGERPRINT in str(info.value)


def test_extra_top_level_keys_are_accepted_and_not_read() -> None:
    data = {**_v1(), "notes": object(), "frozen_on": None}
    assert Config.from_parameter_set(data) == Config.from_parameter_set(_v1())


def test_the_caller_data_is_not_changed() -> None:
    data = _v1()
    before = copy.deepcopy(data)
    Config.from_parameter_set(data)
    assert data == before


def test_type_check_comes_before_key_presence() -> None:
    # Both a non-str key and missing required keys: the key type is named.
    with pytest.raises(ConfigError, match="keys of type int"):
        Config.from_parameter_set({1: 1})  # type: ignore[dict-item]


def test_missing_keys_come_before_the_fingerprint_type() -> None:
    with pytest.raises(ConfigError, match="missing keys: parameters, params_version$"):
        Config.from_parameter_set({"parameter_fingerprint": 1})


def test_fingerprint_type_comes_before_parameters_type() -> None:
    data = {**_v1(), "parameter_fingerprint": 1, "parameters": []}
    with pytest.raises(ConfigError, match="parameter_fingerprint must be exactly a str"):
        Config.from_parameter_set(data)


def test_parameters_key_type_comes_before_the_label_check() -> None:
    data = _v1()
    data["parameters"] = {**_parameters(data), 1: 1, "version": "x"}
    with pytest.raises(ConfigError, match="keys of type int"):
        Config.from_parameter_set(data)


def test_label_check_comes_before_from_dict() -> None:
    data = {**_v1(), "parameters": {"version": "x"}}
    with pytest.raises(ConfigError, match="must not hold version"):
        Config.from_parameter_set(data)


def test_label_form_comes_before_from_dict() -> None:
    data = {**_v1(), "params_version": "v01", "parameters": {}}
    with pytest.raises(ConfigError, match="params_version must be v<N>"):
        Config.from_parameter_set(data)


def test_label_form_comes_before_the_fingerprint_match() -> None:
    data = {**_v1(), "params_version": "v01", "parameter_fingerprint": "0" * 16}
    with pytest.raises(ConfigError, match="params_version must be v<N>"):
        Config.from_parameter_set(data)


# -- criterion 5: every published file loads under its own name ------------------


def test_every_published_file_loads_under_its_name() -> None:
    paths = sorted(PARAMS_DIR.glob("*.json"))
    assert V1_PATH in paths
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = Config.from_parameter_set(data)
        assert path.name == f"{cfg.params_version}.json"


def _register_section(label: str) -> list[str]:
    """The lines of PARAMETERS.md under the whole line "## <label>", up to the next "## "."""
    lines = (ROOT / "PARAMETERS.md").read_text(encoding="utf-8").splitlines()
    heading = f"## {label}"
    assert lines.count(heading) == 1, f"PARAMETERS.md has no single heading line {heading!r}"
    rest = lines[lines.index(heading) + 1 :]
    ends = [i for i, line in enumerate(rest) if line.startswith("## ")]
    return rest[: ends[0]] if ends else rest


def test_every_published_file_is_in_the_register() -> None:
    for path in sorted(PARAMS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        label = data["params_version"]
        fingerprint = data["parameter_fingerprint"]
        line = f"File: `params/{label}.json`. Parameter fingerprint: `{fingerprint}`."
        assert line in _register_section(label)


@pytest.mark.parametrize(
    "text",
    [
        "## v10\n\nFile: `params/v1.json`. Parameter fingerprint: `f9149f3c38a01efc`.\n",
        "### v1\n\nFile: `params/v1.json`. Parameter fingerprint: `f9149f3c38a01efc`.\n",
        "## v1\n\nParameter fingerprint: `1058623faf5d9367`, not `f9149f3c38a01efc`.\n",
    ],
)
def test_the_register_check_needs_the_exact_heading_and_line(
    text: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "PARAMETERS.md").write_text(text, encoding="utf-8")
    monkeypatch.setattr(f"{__name__}.ROOT", tmp_path)
    with pytest.raises(AssertionError):
        test_every_published_file_is_in_the_register()


def test_params_holds_only_json_files() -> None:
    assert sorted(p.suffix for p in PARAMS_DIR.iterdir()) == [".json"] * len(
        list(PARAMS_DIR.iterdir())
    )


# -- criterion 6: a player summary names the parameter set ----------------------


def _ledger() -> list[Match]:
    ids = ["syn-p1", "syn-p2", "syn-p3", "syn-p4"]
    rows = []
    for i in range(6):
        rows.append(
            Match(
                f"syn-m{i}",
                (ids[i % 4], ids[(i + 1) % 4]),
                (ids[(i + 2) % 4], ids[(i + 3) % 4]),
                ((11, 4 + i % 5),),
                START + timedelta(days=3 * i),
                RatingFormat.DOUBLES,
                match_type=MatchType.TOURNAMENT,
            )
        )
    return rows


def test_player_summary_ends_with_the_parameter_set_fields() -> None:
    names = [f.name for f in fields(PlayerSummary)]
    assert names[-3:] == ["connectivity", "params_version", "parameter_fingerprint"]


@pytest.mark.parametrize(
    ("load", "label", "fingerprint"),
    [(False, None, DEFAULT_FINGERPRINT), (True, "v1", V1_FINGERPRINT)],
)
def test_player_summary_carries_the_config_label_and_fingerprint(
    load: bool, label: str | None, fingerprint: str
) -> None:
    cfg = Config.from_parameter_set(_v1()) if load else Config()
    engine = replay(_ledger(), cfg=cfg, as_of=START + timedelta(days=30))
    summary = engine.player_summary("syn-p1", RatingFormat.DOUBLES)
    assert summary.params_version == label
    assert summary.parameter_fingerprint == fingerprint
    assert summary.config_version == __version__


def test_player_summary_of_a_fresh_engine_names_the_set() -> None:
    engine = RatingEngine(Config.from_parameter_set(_v1()))
    engine.seed_provisional_rating("syn-p9", RatingFormat.DOUBLES)
    summary = engine.player_summary("syn-p9", RatingFormat.DOUBLES, as_of=START)
    assert (summary.params_version, summary.parameter_fingerprint) == ("v1", V1_FINGERPRINT)


# -- criterion 7: Config() defaults are unchanged -------------------------------


def test_default_config_is_not_v1() -> None:
    cfg = Config()
    assert cfg.params_version is None
    assert cfg.parameter_fingerprint() == DEFAULT_FINGERPRINT
    assert cfg != Config.from_parameter_set(_v1())
