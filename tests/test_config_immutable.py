"""Config is read-only after creation, hashable, and round-trips through a dict (issue #25)."""

from __future__ import annotations

import copy
import json
import pickle
from collections.abc import Callable, Mapping
from dataclasses import fields, replace
from typing import Any

import pytest

from open_engine import Config, ConfigError, MatchType, ScoringType

DEFAULT_FINGERPRINT = "17f2981186edf96c"

MAPPING_FIELDS = (
    "scale_by_scoring",
    "win_calibration_by_scoring",
    "match_weights",
    "game_length_weights",
)

MUTATIONS: dict[str, Callable[[Any, Any], object]] = {
    "setitem": lambda m, k: m.__setitem__(k, 9.0),
    "delitem": lambda m, k: m.__delitem__(k),
    "update": lambda m, k: m.update({k: 9.0}),
    "clear": lambda m, k: m.clear(),
    "pop": lambda m, k: m.pop(k),
    "setdefault": lambda m, k: m.setdefault(k, 9.0),
}


def _custom() -> Config:
    return Config(
        scale_by_scoring={ScoringType.SIDE_OUT: 2.5, ScoringType.RALLY: 3.1},
        win_calibration_by_scoring={ScoringType.SIDE_OUT: 1.2, ScoringType.RALLY: 0.8},
        match_weights={
            MatchType.TOURNAMENT: 1.0,
            MatchType.LEAGUE: 0.8,
            MatchType.REC_VERIFIED: 0.6,
            MatchType.SELF_REPORTED: 0.3,
        },
        game_length_weights={11: 1.0, 15: 1.05, 21: 1.25, 25: 1.4},
        allowed_game_formats=frozenset({(ScoringType.RALLY, 15), (ScoringType.SIDE_OUT, 11)}),
        max_singles_rating_gap=0.75,
        max_doubles_partner_gap=1.0,
        reject_content_duplicates=True,
        win_weight=0.25,
    )


CONFIGS: dict[str, Callable[[], Config]] = {"default": Config, "custom": _custom}


# -- read-only mappings -----------------------------------------------------------


@pytest.mark.parametrize("mutation", sorted(MUTATIONS))
@pytest.mark.parametrize("name", MAPPING_FIELDS)
def test_mapping_fields_cannot_be_mutated(name: str, mutation: str) -> None:
    cfg = Config()
    before = cfg.fingerprint()
    mapping = getattr(cfg, name)
    key = next(iter(mapping))
    with pytest.raises((TypeError, AttributeError)):
        MUTATIONS[mutation](mapping, key)
    assert cfg.fingerprint() == before
    assert getattr(cfg, name) == getattr(Config(), name)


def test_issue_reproduction_raises_and_keeps_the_fingerprint() -> None:
    cfg = Config()
    with pytest.raises(TypeError):
        cfg.match_weights[MatchType.LEAGUE] = 9  # type: ignore[index]
    assert cfg.fingerprint() == DEFAULT_FINGERPRINT


def test_config_copies_the_dict_it_was_given() -> None:
    weights = {11: 1.0, 15: 1.1, 21: 1.2}
    cfg = Config(game_length_weights=weights)
    before = cfg.fingerprint()
    weights[11] = 5.0
    weights[99] = 1.0
    assert cfg.game_length_weights[11] == 1.0
    assert 99 not in cfg.game_length_weights
    assert cfg.fingerprint() == before


def test_a_mutable_set_of_formats_is_frozen() -> None:
    formats = {(ScoringType.RALLY, 11)}
    cfg = Config(allowed_game_formats=formats)  # type: ignore[arg-type]
    formats.add((ScoringType.SIDE_OUT, 21))
    assert isinstance(cfg.allowed_game_formats, frozenset)
    assert cfg.allowed_game_formats == frozenset({(ScoringType.RALLY, 11)})


@pytest.mark.parametrize("value", [[(ScoringType.RALLY, 11)], "rally", None])
def test_formats_that_are_not_a_set_raise(value: object) -> None:
    with pytest.raises(ConfigError, match="allowed_game_formats must be a set"):
        Config(allowed_game_formats=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", MAPPING_FIELDS)
@pytest.mark.parametrize("value", [[11, 15, 21], "x", None, 3])
def test_mapping_fields_need_a_mapping(name: str, value: object) -> None:
    with pytest.raises(ConfigError, match=f"{name} must be a mapping"):
        Config(**{name: value})


def test_default_fingerprint_is_unchanged() -> None:
    assert Config().fingerprint() == DEFAULT_FINGERPRINT


def test_mapping_insertion_order_does_not_change_the_config() -> None:
    forward = Config(game_length_weights={11: 1.0, 15: 1.1, 21: 1.2})
    backward = Config(game_length_weights={21: 1.2, 15: 1.1, 11: 1.0})
    assert forward == backward
    assert hash(forward) == hash(backward)
    assert forward.fingerprint() == backward.fingerprint()


# -- equality and hashing ---------------------------------------------------------


def test_config_is_hashable() -> None:
    assert hash(Config()) == hash(Config())
    assert {Config(), Config()} == {Config()}
    assert {Config(): "a"}[Config()] == "a"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (Config(), Config()),
        (_custom(), _custom()),
        (Config(k_max=0.5), Config(k_max=0.5)),
    ],
)
def test_equal_configs_share_hash_and_fingerprint(left: Config, right: Config) -> None:
    assert left == right
    assert hash(left) == hash(right)
    assert left.fingerprint() == right.fingerprint()


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (Config(), Config(k_max=0.5)),
        (Config(), _custom()),
        (Config(k_min=0), Config(k_min=0.0)),
        (Config(game_count_bonus=0.12), Config(game_count_bonus=0.13)),
    ],
)
def test_configs_with_different_fingerprints_are_not_equal(left: Config, right: Config) -> None:
    assert left.fingerprint() != right.fingerprint()
    assert left != right


def test_config_is_not_equal_to_other_types() -> None:
    cfg = Config()
    assert cfg != cfg.to_dict()
    assert cfg != cfg.fingerprint()
    assert cfg != object()


# -- copy, pickle, replace ----------------------------------------------------------


@pytest.mark.parametrize("make", CONFIGS.values(), ids=list(CONFIGS))
@pytest.mark.parametrize(
    "clone",
    [copy.copy, copy.deepcopy, lambda c: pickle.loads(pickle.dumps(c)), lambda c: replace(c)],
    ids=["copy", "deepcopy", "pickle", "replace"],
)
def test_copies_are_equal_and_still_read_only(
    make: Callable[[], Config], clone: Callable[[Config], Config]
) -> None:
    cfg = make()
    other = clone(cfg)
    assert other == cfg
    assert other.fingerprint() == cfg.fingerprint()
    assert hash(other) == hash(cfg)
    with pytest.raises(TypeError):
        other.match_weights[MatchType.LEAGUE] = 9  # type: ignore[index]


def test_replace_with_a_spread_mapping_still_works() -> None:
    cfg = Config()
    trial = replace(cfg, scale_by_scoring={**cfg.scale_by_scoring, ScoringType.RALLY: 3.3})
    assert trial.scale_by_scoring[ScoringType.RALLY] == 3.3
    assert cfg.scale_by_scoring[ScoringType.RALLY] == 2.9
    with pytest.raises(TypeError):
        trial.scale_by_scoring[ScoringType.RALLY] = 1.0  # type: ignore[index]


def test_mapping_fields_still_read_like_mappings() -> None:
    cfg = Config()
    assert isinstance(cfg.match_weights, Mapping)
    assert dict(cfg.match_weights)[MatchType.LEAGUE] == 0.90
    assert len(cfg.game_length_weights) == 3
    assert cfg.game_length_weights.get(15) == 1.10
    assert 21 in cfg.game_length_weights
    assert cfg.scale_by_scoring == {ScoringType.SIDE_OUT: 2.9, ScoringType.RALLY: 2.9}


# -- to_dict and from_dict ----------------------------------------------------------


def _json_only(value: object) -> bool:
    if isinstance(value, dict):
        return all(isinstance(k, str) and _json_only(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_json_only(v) for v in value)
    return value is None or type(value) in (str, int, float, bool)


@pytest.mark.parametrize("make", CONFIGS.values(), ids=list(CONFIGS))
def test_to_dict_is_plain_json_data(make: Callable[[], Config]) -> None:
    data = make().to_dict()
    assert _json_only(data)
    assert json.loads(json.dumps(data)) == data


@pytest.mark.parametrize("make", CONFIGS.values(), ids=list(CONFIGS))
def test_round_trip_keeps_equality_and_fingerprint(make: Callable[[], Config]) -> None:
    cfg = make()
    direct = Config.from_dict(cfg.to_dict())
    through_json = Config.from_dict(json.loads(json.dumps(cfg.to_dict())))
    for other in (direct, through_json):
        assert other == cfg
        assert other.fingerprint() == cfg.fingerprint()
        assert hash(other) == hash(cfg)
        assert dict(other.scale_by_scoring) == dict(cfg.scale_by_scoring)
        assert dict(other.game_length_weights) == dict(cfg.game_length_weights)
        assert other.allowed_game_formats == cfg.allowed_game_formats
        assert other.max_singles_rating_gap == cfg.max_singles_rating_gap


def test_to_dict_returns_a_new_dict_each_call() -> None:
    cfg = Config()
    data = cfg.to_dict()
    data["k_min"] = 0.5
    data["match_weights"]["league"] = 9  # type: ignore[index]
    assert cfg.to_dict()["k_min"] == 0.12
    assert cfg.fingerprint() == DEFAULT_FINGERPRINT


def _bad(key: str, value: object) -> dict[str, object]:
    data = Config().to_dict()
    data[key] = value
    return data


def _without(key: str) -> dict[str, object]:
    data = Config().to_dict()
    del data[key]
    return data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([], "must be a mapping"),
        ("k_min", "must be a mapping"),
        (_without("k_min"), "missing keys: k_min"),
        ({**Config().to_dict(), "extra": 1}, "unknown keys: extra"),
        (_bad("scale_by_scoring", {"side_out": 2.9, "pickle": 2.9}), "scale_by_scoring"),
        (_bad("scale_by_scoring", [2.9, 2.9]), "scale_by_scoring"),
        (_bad("match_weights", {"tournament": 1.0}), "match_weights"),
        (_bad("game_length_weights", {"eleven": 1.0, "15": 1.1, "21": 1.2}), "game_length"),
        (_bad("game_length_weights", {"true": 1.0, "15": 1.1, "21": 1.2}), "game_length"),
        (_bad("allowed_game_formats", [["rally"]]), "allowed_game_formats"),
        (_bad("allowed_game_formats", [["rally", "11"]]), "allowed_game_formats"),
        (_bad("allowed_game_formats", [["rally", True]]), "allowed_game_formats"),
        (_bad("allowed_game_formats", [["bounce", 11]]), "allowed_game_formats"),
        (_bad("allowed_game_formats", "rally"), "allowed_game_formats"),
        (_bad("k_min", 0.9), "K-factor bounds are invalid"),
        (_bad("k_min", "low"), "Config"),
    ],
)
def test_from_dict_rejects_bad_data(data: object, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Config.from_dict(data)  # type: ignore[arg-type]


def test_from_dict_accepts_int_game_length_keys() -> None:
    data = Config().to_dict()
    data["game_length_weights"] = {11: 1.0, 15: 1.1, 21: 1.2}
    assert Config.from_dict(data) == Config()


def test_every_mapping_field_is_frozen() -> None:
    cfg = Config()
    mapping_fields = sorted(
        f.name for f in fields(cfg) if isinstance(getattr(cfg, f.name), Mapping)
    )
    assert mapping_fields == sorted(MAPPING_FIELDS)
    for name in mapping_fields:
        assert not isinstance(getattr(cfg, name), dict)


# -- review round 1: keys and values that could share a fingerprint or change --------


@pytest.mark.parametrize("key", ["11", 11.0, True, None])
def test_game_length_keys_must_be_ints(key: object) -> None:
    weights: dict[object, float] = {15: 1.1, 21: 1.2, key: 1.0}
    if key != 11:
        weights[11] = 1.0
    with pytest.raises(ConfigError, match="game_length_weights keys must be whole numbers"):
        Config(game_length_weights=weights)  # type: ignore[arg-type]


def test_a_string_key_no_longer_shares_a_fingerprint_with_an_int_key() -> None:
    with pytest.raises(ConfigError):
        Config(game_length_weights={11: 1.0, 15: 1.1, 21: 1.2, "11": 9.0})  # type: ignore[dict-item]


@pytest.mark.parametrize("name", MAPPING_FIELDS)
@pytest.mark.parametrize("bad", [[0.9], "0.9", True, None, float("nan"), float("inf")])
def test_mapping_values_must_be_finite_numbers(name: str, bad: object) -> None:
    values = dict(getattr(Config(), name))
    values[next(iter(values))] = bad
    with pytest.raises(ConfigError, match=f"{name} values must be finite numbers"):
        Config(**{name: values})


def test_a_mutable_value_cannot_change_the_config() -> None:
    weights: dict[MatchType, object] = dict(Config().match_weights)
    weights[MatchType.LEAGUE] = [0.9]
    with pytest.raises(ConfigError):
        Config(match_weights=weights)  # type: ignore[arg-type]


def test_from_dict_rejects_game_length_keys_that_name_the_same_length() -> None:
    data = Config().to_dict()
    data["game_length_weights"] = {"11": 1.0, "011": 3.0, "15": 1.1, "21": 1.2}
    with pytest.raises(ConfigError, match="game_length_weights repeats"):
        Config.from_dict(data)


@pytest.mark.parametrize(
    "entry",
    [
        (ScoringType.RALLY, 11.0),
        (ScoringType.RALLY, True),
        (ScoringType.RALLY, "11"),
        ("bounce", 11),
        ("rally",),
        "rally",
    ],
)
def test_allowed_game_format_entries_must_be_scoring_and_whole_points(entry: object) -> None:
    with pytest.raises(ConfigError, match="allowed_game_formats entries must be"):
        Config(allowed_game_formats=frozenset({entry}))  # type: ignore[arg-type]
