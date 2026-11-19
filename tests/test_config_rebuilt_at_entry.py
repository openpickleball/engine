"""Every public entry point that takes a Config uses a rebuilt copy (issue #122).

An exact Config can be rewritten with object.__setattr__ after __post_init__
checked it. Each entry point now builds a fresh Config from the given one's
field values, so every check and the #107 plain-value conversion run again,
and the engine keeps only that copy. All ids are synthetic.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, ItemsView, Iterator, KeysView, Mapping, ValuesView
from dataclasses import asdict
from datetime import date
from typing import cast

import pytest

import open_engine._config as config_module
from open_engine import (
    Config,
    ConfigError,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    RatingState,
    ScoringType,
    evaluate,
    fit_scale,
    fit_win_calibration,
    prequential_predictions,
    reliability,
    replay,
)
from open_engine._config import _FrozenMapping
from open_engine.engine import ALL_GAME_FORMATS
from test_exact_input_plain_unchanged import _ledger, _seeds

D = RatingFormat.DOUBLES
AS_OF = date(2026, 6, 1)
MID = date(2026, 3, 15)
TOURNAMENT = Match(
    "syn-m1",
    ("syn-a", "syn-b"),
    ("syn-c", "syn-d"),
    ((11, 5),),
    date(2026, 1, 1),
    D,
    match_type=MatchType.TOURNAMENT,
)
LATE_TOURNAMENT = Match(
    "syn-late",
    ("syn-imp", "syn-b"),
    ("syn-c", "syn-d"),
    ((11, 4),),
    date(2026, 7, 1),
    D,
    match_type=MatchType.TOURNAMENT,
)

# Caller code that runs in a tampered value records its name here.
CALLS: list[str] = []


@pytest.fixture(autouse=True)
def _clear_calls() -> Iterator[None]:
    CALLS.clear()
    yield
    CALLS.clear()


class Big(int):
    """An int subclass that overrides arithmetic, as in the issue."""

    def __mul__(self, other: object) -> int:
        return 10**6

    __rmul__ = __mul__

    def __truediv__(self, other: object) -> int:
        return 10**6

    __rtruediv__ = __truediv__


def _sha(engine: RatingEngine) -> str:
    text = json.dumps(engine.to_snapshot(), sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _tamper(cfg: Config, name: str, value: object) -> Config:
    object.__setattr__(cfg, name, value)
    return cfg


def _issue_repro() -> Config:
    """The issue's config: match_weights replaced by a dict holding Big(1)."""
    cfg = Config()
    weights = dict(cfg.match_weights)
    weights[MatchType.TOURNAMENT] = Big(1)
    return _tamper(cfg, "match_weights", weights)


def _tampered() -> Config:
    """The issue repro, plus a Big diversity_target that reliability reads."""
    return _tamper(_issue_repro(), "diversity_target", Big(12))


def _reference() -> Config:
    """The same values given to the constructor, which makes each plain (#107)."""
    weights = {**Config().match_weights, MatchType.TOURNAMENT: Big(1)}
    return Config(match_weights=weights, diversity_target=Big(12))


# -- every entry point, as a function of cfg ---------------------------------


def _snapshots(cfg: Config) -> tuple[dict[str, object], dict[str, object]]:
    full = replay(_ledger(), _seeds(), cfg=cfg, as_of=AS_OF)
    mid = replay(_ledger(), _seeds(), cfg=cfg, as_of=MID)
    return full.to_snapshot(), mid.to_snapshot()


def _state() -> RatingState:
    engine = replay(_ledger(), _seeds(), as_of=AS_OF)
    return engine.players["syn-b"].ratings[D]


def _entry_points(
    full: dict[str, object], mid: dict[str, object]
) -> dict[str, Callable[[Config | None], object]]:
    state = _state()
    rows = _ledger()
    seeds = _seeds()

    def engine(cfg: Config | None) -> object:
        return RatingEngine(cfg).record(TOURNAMENT).deltas

    def from_snapshot(cfg: Config | None) -> object:
        restored = RatingEngine.from_snapshot(full, cfg)
        return restored.record(LATE_TOURNAMENT).deltas

    def replay_full(cfg: Config | None) -> object:
        result = replay(rows, seeds, cfg=cfg, as_of=AS_OF)
        assert result.replay_info is not None
        return _sha(result), result.replay_info.config_fingerprint

    def replay_resume(cfg: Config | None) -> object:
        return _sha(replay(rows, cfg=cfg, as_of=AS_OF, from_snapshot=mid))

    def prequential_live(cfg: Config | None) -> object:
        return prequential_predictions(rows, seeds, cfg=cfg, burn_in_matches=2)

    def prequential_scheduled(cfg: Config | None) -> object:
        return prequential_predictions(rows, seeds, cfg=cfg, burn_in_matches=2, mode="scheduled")

    def evaluate_(cfg: Config | None) -> object:
        return asdict(evaluate(rows, seeds, cfg=cfg, burn_in_matches=2))

    def fit_scale_(cfg: Config | None) -> object:
        fit = fit_scale(rows, seeds, cfg=cfg, burn_in_matches=2, low=2.0, high=4.0, tolerance=0.5)
        return asdict(fit)

    def fit_win_calibration_(cfg: Config | None) -> object:
        fit = fit_win_calibration(rows, seeds, cfg=cfg, burn_in_matches=2, tolerance=0.05)
        return asdict(fit)

    def reliability_(cfg: Config | None) -> object:
        return reliability(state, cast(Config, cfg), AS_OF)

    return {
        "RatingEngine": engine,
        "from_snapshot": from_snapshot,
        "replay": replay_full,
        "replay_resume": replay_resume,
        "prequential_live": prequential_live,
        "prequential_scheduled": prequential_scheduled,
        "evaluate": evaluate_,
        "fit_scale": fit_scale_,
        "fit_win_calibration": fit_win_calibration_,
        "reliability": reliability_,
    }


ENTRY_NAMES = [
    "RatingEngine",
    "from_snapshot",
    "replay",
    "replay_resume",
    "prequential_live",
    "prequential_scheduled",
    "evaluate",
    "fit_scale",
    "fit_win_calibration",
    "reliability",
]


@pytest.fixture(scope="module")
def reference_points() -> dict[str, Callable[[Config | None], object]]:
    full, mid = _snapshots(_reference())
    return _entry_points(full, mid)


@pytest.fixture(scope="module")
def plain_points() -> dict[str, Callable[[Config | None], object]]:
    full, mid = _snapshots(Config())
    return _entry_points(full, mid)


# -- criterion 3: a rewritten value is checked and converted again ----------


def test_issue_repro_rates_like_the_plain_config() -> None:
    plain = RatingEngine(Config()).record(TOURNAMENT).deltas
    assert RatingEngine(_issue_repro()).record(TOURNAMENT).deltas == plain


@pytest.mark.parametrize("name", ENTRY_NAMES)
def test_tampered_config_rates_like_the_constructor_built_one(
    name: str, reference_points: dict[str, Callable[[Config | None], object]]
) -> None:
    point = reference_points[name]
    assert point(_tampered()) == point(_reference())


def _negative_weight_dict() -> Config:
    weights = {**Config().match_weights, MatchType.TOURNAMENT: -5.0}
    return _tamper(Config(), "match_weights", weights)


def _negative_weight_inside() -> Config:
    cfg = Config()
    weights = {**cfg.match_weights, MatchType.TOURNAMENT: -5.0}
    object.__setattr__(cfg.match_weights, "_data", weights)
    return cfg


REJECTED: list[tuple[str, Callable[[], Config], str]] = [
    ("k_max negative", lambda: _tamper(Config(), "k_max", -1.0), "K-factor bounds are invalid"),
    ("k_max str", lambda: _tamper(Config(), "k_max", "x"), "k_max must be a finite number"),
    ("weight dict", _negative_weight_dict, "match_weights values must be positive"),
    ("weight inside", _negative_weight_inside, "match_weights values must be positive"),
]


@pytest.mark.parametrize("name", ENTRY_NAMES)
@pytest.mark.parametrize(
    ("make", "message"), [(m, msg) for _, m, msg in REJECTED], ids=[i for i, _, _ in REJECTED]
)
def test_rewritten_value_config_rejects_raises(
    name: str,
    make: Callable[[], Config],
    message: str,
    plain_points: dict[str, Callable[[Config | None], object]],
) -> None:
    with pytest.raises(ConfigError, match=message):
        plain_points[name](make())


# -- criterion 2: the rebuild never runs caller code -------------------------


class SpyDict(dict[object, object]):
    """A dict subclass whose read methods record that they ran."""

    def __getitem__(self, key: object) -> object:
        CALLS.append("__getitem__")
        return super().__getitem__(key)

    def __iter__(self) -> Iterator[object]:
        CALLS.append("__iter__")
        return super().__iter__()

    def __len__(self) -> int:
        CALLS.append("__len__")
        return super().__len__()

    def items(self) -> ItemsView[object, object]:
        CALLS.append("items")
        return super().items()

    def keys(self) -> KeysView[object]:
        CALLS.append("keys")
        return super().keys()

    def values(self) -> ValuesView[object]:
        CALLS.append("values")
        return super().values()


class SpyMapping(Mapping[object, object]):
    """A Mapping that records every read."""

    def __init__(self, data: Mapping[object, object]) -> None:
        self._held = dict(data)

    def __getitem__(self, key: object) -> object:
        CALLS.append("__getitem__")
        return self._held[key]

    def __iter__(self) -> Iterator[object]:
        CALLS.append("__iter__")
        return iter(self._held)

    def __len__(self) -> int:
        CALLS.append("__len__")
        return len(self._held)


class SpyObject:
    """An object that records any attribute read or protocol call."""

    def __getattribute__(self, name: str) -> object:
        CALLS.append(name)
        return object.__getattribute__(self, name)

    def __getitem__(self, key: object) -> object:
        CALLS.append("__getitem__")
        return 1.0

    def __iter__(self) -> Iterator[object]:
        CALLS.append("__iter__")
        return iter(())

    def __len__(self) -> int:
        CALLS.append("__len__")
        return 0

    def __eq__(self, other: object) -> bool:
        CALLS.append("__eq__")
        return False

    def __hash__(self) -> int:
        CALLS.append("__hash__")
        return 0


class SubFrozen(_FrozenMapping[object, object]):
    """A _FrozenMapping subclass that records reads."""

    __slots__ = ()

    def __getitem__(self, key: object) -> object:
        CALLS.append("__getitem__")
        return super().__getitem__(key)


class SpySet(set[object]):
    """A set subclass whose iteration records that it ran."""

    def __iter__(self) -> Iterator[object]:
        CALLS.append("__iter__")
        return super().__iter__()


def _inner(value: object) -> Config:
    cfg = Config()
    object.__setattr__(cfg.match_weights, "_data", value)
    return cfg


def _inner_deleted() -> Config:
    cfg = Config()
    object.__delattr__(cfg.match_weights, "_data")
    return cfg


def _weights() -> dict[object, object]:
    return dict(Config().match_weights)


UNREAD: list[tuple[str, Callable[[], Config], str]] = [
    ("_data list", lambda: _inner(list(_weights())), "match_weights"),
    ("_data dict subclass", lambda: _inner(SpyDict(_weights())), "match_weights"),
    ("_data spy object", lambda: _inner(SpyObject()), "match_weights"),
    ("_data deleted", _inner_deleted, "match_weights"),
    (
        "field Mapping",
        lambda: _tamper(Config(), "match_weights", SpyMapping(_weights())),
        "match_weights",
    ),
    (
        "field dict subclass",
        lambda: _tamper(Config(), "match_weights", SpyDict(_weights())),
        "match_weights",
    ),
    (
        "field _FrozenMapping subclass",
        lambda: _tamper(Config(), "match_weights", SubFrozen(_weights())),
        "match_weights",
    ),
    (
        "field spy object",
        lambda: _tamper(Config(), "game_length_weights", SpyObject()),
        "game_length_weights",
    ),
    (
        "formats set subclass",
        lambda: _tamper(Config(), "allowed_game_formats", SpySet(ALL_GAME_FORMATS)),
        "allowed_game_formats",
    ),
    (
        "formats list",
        lambda: _tamper(Config(), "allowed_game_formats", list(ALL_GAME_FORMATS)),
        "allowed_game_formats",
    ),
]


@pytest.mark.parametrize("name", ENTRY_NAMES)
@pytest.mark.parametrize(
    ("make", "message"), [(m, msg) for _, m, msg in UNREAD], ids=[i for i, _, _ in UNREAD]
)
def test_rebuild_rejects_odd_containers_without_running_their_code(
    name: str,
    make: Callable[[], Config],
    message: str,
    plain_points: dict[str, Callable[[Config | None], object]],
) -> None:
    cfg = make()
    CALLS.clear()
    with pytest.raises(ConfigError, match=message):
        plain_points[name](cfg)
    assert CALLS == []


# -- criteria 1 and 6: the engine holds its own copy -------------------------


def test_engine_holds_an_equal_copy() -> None:
    cfg = Config(k_max=0.5)
    engine = RatingEngine(cfg)
    assert engine.cfg is not cfg
    assert engine.cfg == cfg
    assert engine.cfg.fingerprint() == cfg.fingerprint()


def test_replay_and_from_snapshot_hold_an_equal_copy() -> None:
    cfg = Config(k_max=0.5)
    result = replay(_ledger(), _seeds(), cfg=cfg, as_of=AS_OF)
    restored = RatingEngine.from_snapshot(result.to_snapshot(), cfg)
    for held in (result.cfg, restored.cfg):
        assert held is not cfg
        assert held == cfg
        assert held.fingerprint() == cfg.fingerprint()


def test_tampering_the_caller_config_after_rating_engine_changes_nothing() -> None:
    cfg = Config()
    engine = RatingEngine(cfg)
    _tamper(cfg, "k_max", 5.0)
    assert engine.record(TOURNAMENT).deltas == RatingEngine().record(TOURNAMENT).deltas


def test_tampering_the_caller_config_after_replay_changes_nothing() -> None:
    cfg = Config()
    result = replay(_ledger(), _seeds(), cfg=cfg, as_of=AS_OF)
    before = result.to_snapshot()
    _tamper(cfg, "k_max", 5.0)
    assert result.to_snapshot() == before
    untouched = replay(_ledger(), _seeds(), as_of=AS_OF)
    assert result.record(LATE_TOURNAMENT).deltas == untouched.record(LATE_TOURNAMENT).deltas


# -- criterion 5: one rebuild per public call ---------------------------------


@pytest.mark.parametrize("name", ENTRY_NAMES)
def test_each_public_call_rebuilds_once(
    name: str,
    plain_points: dict[str, Callable[[Config | None], object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = config_module._rebuilt_config
    seen: list[Config] = []

    def counted(cfg: Config) -> Config:
        seen.append(cfg)
        return original(cfg)

    monkeypatch.setattr(config_module, "_rebuilt_config", counted)
    cfg = Config()
    plain_points[name](cfg)
    assert seen == [cfg]
    assert seen[0] is cfg


@pytest.mark.parametrize("name", [n for n in ENTRY_NAMES if n != "reliability"])
def test_a_none_config_is_not_rebuilt(
    name: str,
    plain_points: dict[str, Callable[[Config | None], object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = config_module._rebuilt_config
    seen: list[Config] = []

    def counted(cfg: Config) -> Config:
        seen.append(cfg)
        return original(cfg)

    monkeypatch.setattr(config_module, "_rebuilt_config", counted)
    plain_points[name](None)
    assert seen == []


# -- criterion 4: an untouched non-default config gives the same output ------


def _pin_config() -> Config:
    """Non-default values: int mapping values, a plain set of formats."""
    return Config(
        k_max=0.5,
        scale_by_scoring={ScoringType.SIDE_OUT: 3, ScoringType.RALLY: 2.5},
        win_calibration_by_scoring={ScoringType.SIDE_OUT: 1, ScoringType.RALLY: 1.25},
        match_weights={
            MatchType.TOURNAMENT: 2,
            MatchType.LEAGUE: 1.5,
            MatchType.REC_VERIFIED: 1,
            MatchType.SELF_REPORTED: 1,
        },
        game_length_weights={11: 1, 15: 1.1, 21: 2},
        allowed_game_formats=set(ALL_GAME_FORMATS),
        max_information_multiplier=2,
    )


# Measured on a1490cd (the same engine as aa3a2d0), before the rebuild.
PIN_FINGERPRINT = "5b30905357e82e84"
PIN_REPLAY_SHA = "3956aa9b52e6e01c9ee4fcc301223216637ef445732e2725479c16b42f88e59a"
PIN_MID_SHA = "beb281fa224c5056b0e89959a355f6809367b166fc2009f547c5645569fd58e2"
PIN_EVALUATE = {
    "scoring": None,
    "scale": None,
    "matches_scored": 42,
    "mse": 0.006697,
    "mae": 0.07075,
    "bias": 0.019051,
    "calibration": ((0.4, 0.4818, 0.5268, 9), (0.5, 0.5358, 0.5478, 33)),
}
PIN_FIT_WIN = {
    "scoring": "side_out",
    "a": 0.793,
    "brier": 0.23632804407148736,
    "brier_uncalibrated": 0.23709196602043178,
    "n": 36,
    "at_bound": False,
}
PIN_RELIABILITY = 73.2


def test_untouched_non_default_config_output_unchanged() -> None:
    cfg = _pin_config()
    assert cfg.fingerprint() == PIN_FINGERPRINT
    full = replay(_ledger(), _seeds(), cfg=cfg, as_of=AS_OF)
    assert full.replay_info is not None
    assert full.replay_info.config_fingerprint == PIN_FINGERPRINT
    assert _sha(full) == PIN_REPLAY_SHA
    mid = replay(_ledger(), _seeds(), cfg=cfg, as_of=MID)
    assert _sha(mid) == PIN_MID_SHA
    resumed = replay(_ledger(), cfg=cfg, as_of=AS_OF, from_snapshot=mid.to_snapshot())
    assert _sha(resumed) == PIN_REPLAY_SHA
    report = evaluate(_ledger(), _seeds(), cfg=cfg, burn_in_matches=2)
    assert asdict(report) == PIN_EVALUATE
    fit = fit_win_calibration(_ledger(), _seeds(), cfg=cfg, burn_in_matches=2, tolerance=0.01)
    assert asdict(fit) == PIN_FIT_WIN
    state = full.players["syn-b"].ratings[D]
    assert reliability(state, cfg, AS_OF) == PIN_RELIABILITY
