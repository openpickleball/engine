"""Parameter set identity independent of the package version (issue #155).

Config.params_version labels a published parameter set, and
Config.parameter_fingerprint() hashes the parameter values without the two
labels. All player and match ids are synthetic.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import fields
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ConfigError,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    ScoringType,
    SnapshotError,
    replay,
)

LABELS = ("version", "params_version")
HEX16 = re.compile(r"[0-9a-f]{16}")
START = date(2026, 3, 2)


class _Str(str):
    pass


# -- criteria 1 and 2: the field and its checks -------------------------------


def test_params_version_defaults_to_none() -> None:
    assert Config().params_version is None


@pytest.mark.parametrize("value", ["v1", "v9", "v12", "v100"])
def test_params_version_accepts_v_and_a_positive_whole_number(value: str) -> None:
    assert Config(params_version=value).params_version == value


@pytest.mark.parametrize("value", [1, b"v1", ["v1"], 1.0, True], ids=repr)
def test_params_version_of_another_type_raises(value: object) -> None:
    with pytest.raises(ConfigError, match=r"^params_version must be None or a str$"):
        Config(params_version=value)  # type: ignore[arg-type]


def test_params_version_str_subclass_raises() -> None:
    with pytest.raises(ConfigError, match=r"^params_version must be None or a str$"):
        Config(params_version=_Str("v1"))


@pytest.mark.parametrize(
    "value",
    ["", "v", "v0", "v01", "1", "V1", "v1 ", " v1", "v1\n", "v-1", "v1.0", "v١", "vv1"],
    ids=repr,
)
def test_malformed_params_version_raises(value: str) -> None:
    with pytest.raises(ConfigError, match=r"^params_version must be None or v<N>"):
        Config(params_version=value)


def test_params_version_type_is_checked_after_version() -> None:
    with pytest.raises(ConfigError, match=r"^version must be a str$"):
        Config(version=1, params_version=1)  # type: ignore[arg-type]


def test_params_version_is_checked_before_the_number_fields() -> None:
    with pytest.raises(ConfigError, match=r"^params_version must be None or a str$"):
        Config(params_version=1, k_min="x")  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match=r"^params_version must be None or v<N>"):
        Config(params_version="v0", k_min="x")  # type: ignore[arg-type]


def test_params_version_follows_version_in_field_order() -> None:
    names = [f.name for f in fields(Config)]
    assert names[:2] == ["version", "params_version"]


# -- criteria 3 and 4: parameter_fingerprint ----------------------------------


def test_parameter_fingerprint_is_16_lowercase_hex_and_deterministic() -> None:
    value = Config().parameter_fingerprint()
    assert HEX16.fullmatch(value)
    assert Config().parameter_fingerprint() == value
    assert Config(params_version="v2").parameter_fingerprint() == value


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ({"version": "0.1.0"}, {"version": "9.9.9"}),
        ({"params_version": None}, {"params_version": "v1"}),
        ({"params_version": "v1"}, {"params_version": "v2"}),
        (
            {"version": "0.1.0", "params_version": "v1"},
            {"version": "2.0.0", "params_version": None},
        ),
    ],
)
def test_parameter_fingerprint_ignores_the_two_labels(
    first: dict[str, object], second: dict[str, object]
) -> None:
    a, b = Config(**first), Config(**second)  # type: ignore[arg-type]
    assert a.parameter_fingerprint() == b.parameter_fingerprint()
    assert a.fingerprint() != b.fingerprint()


def _other_value(name: str, value: object) -> object:
    """A valid value for the named field, different from the default."""
    if name == "allowed_game_formats":
        return frozenset({(ScoringType.SIDE_OUT, 11)})
    if name == "game_length_weights":
        return {11: 1.0, 15: 1.15, 21: 1.2}
    if name == "match_weights":
        return {**dict(Config().match_weights), MatchType.LEAGUE: 0.85}
    if name in ("scale_by_scoring", "win_calibration_by_scoring"):
        return {ScoringType.SIDE_OUT: 2.5, ScoringType.RALLY: 1.1}
    if name in ("max_singles_rating_gap", "max_doubles_partner_gap"):
        return 1.5
    if name == "connectivity_reliability_cap":
        return 50.0
    if name == "rd_max":
        return 0.85
    if name == "new_player_rd":
        return 0.7
    if type(value) is bool:
        return not value
    if type(value) is int:
        return value + 1
    if type(value) is float:
        # A small step keeps every range and cross-field rule satisfied.
        return value + 0.01 if value < 1.0 else value - 0.01
    raise AssertionError(f"no other value for {name}")


@pytest.mark.parametrize("name", [f.name for f in fields(Config) if f.name not in LABELS])
def test_parameter_fingerprint_changes_with_every_other_field(name: str) -> None:
    base = Config()
    changed = Config(**{name: _other_value(name, getattr(base, name))})
    assert getattr(changed, name) != getattr(base, name)
    assert changed.parameter_fingerprint() != base.parameter_fingerprint()
    assert changed.fingerprint() != base.fingerprint()


# -- criterion 5: fingerprint keeps every field -------------------------------


def test_fingerprint_changes_with_version_and_with_params_version() -> None:
    base = Config()
    assert Config(version="0.0.1").fingerprint() != base.fingerprint()
    assert Config(params_version="v1").fingerprint() != base.fingerprint()
    assert Config(params_version="v1").fingerprint() != Config(params_version="v2").fingerprint()


# -- criterion 6: to_dict and from_dict ---------------------------------------


@pytest.mark.parametrize("value", [None, "v3"])
def test_to_dict_round_trips_params_version(value: str | None) -> None:
    cfg = Config(params_version=value)
    data = cfg.to_dict()
    assert data["params_version"] == value
    for loaded in (Config.from_dict(data), Config.from_dict(json.loads(json.dumps(data)))):
        assert loaded == cfg
        assert loaded.params_version == value
        assert loaded.parameter_fingerprint() == cfg.parameter_fingerprint()
        assert loaded.fingerprint() == cfg.fingerprint()


def test_from_dict_without_params_version_raises() -> None:
    data = Config().to_dict()
    del data["params_version"]
    with pytest.raises(ConfigError, match=r"^Config data has missing keys: params_version$"):
        Config.from_dict(data)


def test_from_dict_rejects_a_malformed_params_version() -> None:
    data = Config().to_dict()
    data["params_version"] = "v01"
    with pytest.raises(ConfigError, match=r"^params_version must be None or v<N>"):
        Config.from_dict(data)


# -- criterion 7: replay records both -----------------------------------------


def _ledger() -> list[Match]:
    ids = ["syn-p1", "syn-p2", "syn-p3", "syn-p4"]
    rows = []
    for i in range(6):
        a = (ids[i % 4], ids[(i + 1) % 4])
        b = (ids[(i + 2) % 4], ids[(i + 3) % 4])
        rows.append(
            Match(
                f"syn-m{i}",
                a,
                b,
                ((11, 4 + i % 5),),
                START + timedelta(days=3 * i),
                RatingFormat.DOUBLES,
                match_type=MatchType.TOURNAMENT,
            )
        )
    return rows


@pytest.mark.parametrize("value", [None, "v1"])
def test_replay_info_carries_params_version_and_parameter_fingerprint(value: str | None) -> None:
    cfg = Config(params_version=value)
    info = replay(_ledger(), cfg=cfg, as_of=START + timedelta(days=30)).replay_info
    assert info is not None
    assert info.params_version == value
    assert info.parameter_fingerprint == cfg.parameter_fingerprint()
    assert info.config_fingerprint == cfg.fingerprint()


def test_replay_info_field_order() -> None:
    names = [f.name for f in fields(type(replay(_ledger(), as_of=START).replay_info))]
    at = names.index("config_fingerprint")
    assert names[at + 1 : at + 3] == ["params_version", "parameter_fingerprint"]


# -- criterion 8: snapshots ---------------------------------------------------


AS_OF = START + timedelta(days=30)


def _snapshot(cfg: Config) -> dict[str, object]:
    engine = replay(_ledger(), cfg=cfg, as_of=AS_OF)
    loaded: dict[str, object] = json.loads(json.dumps(engine.to_snapshot()))
    return loaded


def _info(snapshot: dict[str, object]) -> dict[str, object]:
    info = snapshot["replay_info"]
    assert type(info) is dict
    return info


@pytest.mark.parametrize("value", [None, "v4"])
def test_snapshot_writes_version_9_and_round_trips_both_keys(value: str | None) -> None:
    cfg = Config(params_version=value)
    snapshot = _snapshot(cfg)
    assert snapshot["snapshot_version"] == 9
    info = _info(snapshot)
    assert info["params_version"] == value
    assert info["parameter_fingerprint"] == cfg.parameter_fingerprint()
    loaded = RatingEngine.from_snapshot(snapshot, cfg)
    assert loaded.replay_info is not None
    assert loaded.replay_info.params_version == value
    assert loaded.replay_info.parameter_fingerprint == cfg.parameter_fingerprint()
    assert loaded.to_snapshot() == snapshot


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("v2", "params_version must equal the Config's params_version"),
        (None, "params_version must equal the Config's params_version"),
        (1, "params_version must be a str or null"),
        (_Str("v1"), "params_version must be a str or null"),
    ],
    ids=repr,
)
def test_snapshot_rejects_a_tampered_params_version(value: object, message: str) -> None:
    cfg = Config(params_version="v1")
    snapshot = _snapshot(cfg)
    _info(snapshot)["params_version"] = value
    with pytest.raises(SnapshotError, match=f"^snapshot.replay_info.{re.escape(message)}$"):
        RatingEngine.from_snapshot(snapshot, cfg)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("0000000000000000", "parameter_fingerprint must equal the Config's parameter_fingerprint"),
        (None, "parameter_fingerprint must be a str"),
        (7, "parameter_fingerprint must be a str"),
    ],
    ids=repr,
)
def test_snapshot_rejects_a_tampered_parameter_fingerprint(value: object, message: str) -> None:
    cfg = Config()
    snapshot = _snapshot(cfg)
    _info(snapshot)["parameter_fingerprint"] = value
    with pytest.raises(SnapshotError, match=f"^snapshot.replay_info.{re.escape(message)}$"):
        RatingEngine.from_snapshot(snapshot, cfg)


def test_snapshot_under_a_config_with_another_params_version_raises() -> None:
    snapshot = _snapshot(Config(params_version="v1"))
    for other in (Config(), Config(params_version="v2")):
        with pytest.raises(SnapshotError, match=r"^snapshot.config_fingerprint"):
            RatingEngine.from_snapshot(snapshot, other)


@pytest.mark.parametrize("key", ["params_version", "parameter_fingerprint"])
def test_snapshot_replay_info_missing_a_new_key_raises(key: str) -> None:
    snapshot = _snapshot(Config())
    del _info(snapshot)[key]
    with pytest.raises(SnapshotError, match=f"^snapshot.replay_info: missing key '{key}'$"):
        RatingEngine.from_snapshot(snapshot, Config())


def test_snapshot_replay_info_with_an_extra_key_raises() -> None:
    snapshot = _snapshot(Config())
    _info(snapshot)["params_set"] = "v1"
    with pytest.raises(SnapshotError, match=r"^snapshot.replay_info: unknown key 'params_set'$"):
        RatingEngine.from_snapshot(snapshot, Config())


def test_version_8_snapshot_does_not_load() -> None:
    snapshot = _snapshot(Config())
    old = copy.deepcopy(snapshot)
    old["snapshot_version"] = 8
    info = _info(old)
    del info["params_version"]
    del info["parameter_fingerprint"]
    with pytest.raises(SnapshotError, match=r"^snapshot.snapshot_version must be 9, not 8$"):
        RatingEngine.from_snapshot(old, Config())


def test_resume_under_a_params_version_equals_a_full_replay() -> None:
    cfg = Config(params_version="v1")
    ledger = _ledger()
    cut = START + timedelta(days=6)
    checkpoint = json.loads(
        json.dumps(
            replay([m for m in ledger if m.played_on <= cut], cfg=cfg, as_of=cut).to_snapshot()
        )
    )
    resumed = replay(ledger, cfg=cfg, as_of=AS_OF, from_snapshot=checkpoint)
    full = replay(ledger, cfg=cfg, as_of=AS_OF)
    assert json.dumps(resumed.to_snapshot(), sort_keys=True) == json.dumps(
        full.to_snapshot(), sort_keys=True
    )
    assert resumed.replay_info is not None
    assert resumed.replay_info.params_version == "v1"
