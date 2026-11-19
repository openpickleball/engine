"""Second review of the exact input type rule (issues #116, #118, #119).

Config enum keys read more than once, type names read through a caller's
metaclass, arithmetic that overflows after the checks pass, and
Config.from_dict's nested data. All ids are synthetic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date

import pytest

from open_engine import (
    Config,
    ConfigError,
    EvaluationError,
    ExternalRatingSnapshot,
    GameFormat,
    InvalidMatchError,
    InvalidSeedError,
    Match,
    MatchType,
    OpenEngineError,
    PlayerError,
    RatingEngine,
    RatingFormat,
    RatingState,
    ReferenceDateError,
    ScoringType,
    SnapshotError,
    expected_point_share,
    game_win_probability,
    match_win_probability,
    prequential_predictions,
    reliability,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
RALLY = ScoringType.RALLY

# -- K: Config enum mapping keys are read once ------------------------------------


class _Flip(str):
    """Hashes and compares as its own value once, then as another value."""

    calls = 0
    other = ""

    def __hash__(self) -> int:
        _Flip.calls += 1
        return str.__hash__(self) if _Flip.calls <= 1 else str.__hash__(_Flip.other)

    def __eq__(self, other: object) -> bool:
        _Flip.calls += 1
        value = str(self) if _Flip.calls <= 1 else _Flip.other
        return str.__eq__(value, other)  # type: ignore[arg-type]

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)


MAPPINGS: list[tuple[str, type, str, str]] = [
    ("match_weights", MatchType, "tournament", "league"),
    ("scale_by_scoring", ScoringType, "side_out", "rally"),
    ("win_calibration_by_scoring", ScoringType, "side_out", "rally"),
]


@pytest.mark.parametrize(
    ("name", "kind", "value", "other"), MAPPINGS, ids=[name for name, _, _, _ in MAPPINGS]
)
def test_config_rejects_str_subclass_enum_keys(
    name: str, kind: type, value: str, other: str
) -> None:
    _Flip.other = other
    mapping: dict[object, float] = {
        member.value: 1.0
        for member in kind  # type: ignore[attr-defined]
        if member.value != value
    }
    mapping[_Flip(value)] = 2.0
    _Flip.calls = 0
    with pytest.raises(ConfigError, match=name):
        Config(**{name: mapping})  # type: ignore[arg-type]
    assert _Flip.calls == 0


def test_config_keys_still_convert_plain_str_and_members() -> None:
    weights = {"tournament": 1.4, MatchType.LEAGUE: 1.2, "rec_verified": 1.0}
    weights[MatchType.SELF_REPORTED] = 0.5  # type: ignore[index]
    cfg = Config(match_weights=weights)  # type: ignore[arg-type]
    assert all(type(key) is MatchType for key in cfg.match_weights)
    assert cfg.match_weights[MatchType.TOURNAMENT] == 1.4


# -- L: type names never run caller code ------------------------------------------

RAN: list[str] = []


class _LoudMeta(type):
    @property  # type: ignore[misc]
    def __name__(cls) -> str:
        RAN.append("name")
        raise RuntimeError("caller code ran")


class _Loud(metaclass=_LoudMeta):
    pass


def _seeded() -> RatingEngine:
    engine = RatingEngine()
    engine.seed_provisional_rating("syn-a", S)
    return engine


LOUD_CALLS: list[tuple[str, type[OpenEngineError], Callable[[], object]]] = [
    ("replay as_of", ReferenceDateError, lambda: replay([], as_of=_Loud())),  # type: ignore[arg-type]
    ("state format", PlayerError, lambda: _seeded().state("syn-a", _Loud())),  # type: ignore[arg-type]
    ("expected_point_share", EvaluationError, lambda: expected_point_share(_Loud(), 1, 1)),  # type: ignore[arg-type]
    ("RatingEngine cfg", ConfigError, lambda: RatingEngine(_Loud())),  # type: ignore[arg-type]
    ("from_dict", ConfigError, lambda: Config.from_dict(_Loud())),  # type: ignore[arg-type]
    (
        "from_snapshot version",
        SnapshotError,
        lambda: RatingEngine.from_snapshot({"snapshot_version": _Loud()}),
    ),
    (
        "match played_on",
        InvalidMatchError,
        lambda: Match.validate(
            Match("syn-m1", ("syn-a",), ("syn-b",), ((11, 5),), _Loud(), S)  # type: ignore[arg-type]
        ),
    ),
    (
        "seed flag",
        InvalidSeedError,
        lambda: RatingEngine().seed_external_rating(
            "syn-a",
            D,
            ExternalRatingSnapshot("external_standard", 4.0, date(2026, 1, 1)),
            replace_unplayed_seed=_Loud(),  # type: ignore[arg-type]
        ),
    ),
    (
        "match_win best_of",
        EvaluationError,
        lambda: match_win_probability(0.5, GameFormat(), _Loud()),  # type: ignore[arg-type]
    ),
]


@pytest.mark.parametrize(
    ("label", "error", "call"), LOUD_CALLS, ids=[label for label, _, _ in LOUD_CALLS]
)
def test_type_names_never_run_caller_code(
    label: str, error: type[OpenEngineError], call: Callable[[], object]
) -> None:
    RAN.clear()
    with pytest.raises(error, match="_Loud"):
        call()
    assert RAN == []


# -- M: arithmetic that overflows after the checks pass ---------------------------


def _rally_600_config() -> Config:
    data = json.loads(json.dumps(Config().to_dict()))
    data["allowed_game_formats"].append(["rally", 600])
    data["game_length_weights"]["600"] = 1.0
    return Config.from_dict(data)


OVERFLOWS: list[tuple[str, Callable[[], object]]] = [
    ("game first to 600", lambda: game_win_probability(0.5, GameFormat(RALLY, 600, 1))),
    ("game win by 2 at 600", lambda: game_win_probability(0.5, GameFormat(RALLY, 600, 2))),
    ("match at 600", lambda: match_win_probability(0.5, GameFormat(RALLY, 600, 1), 3)),
    ("share", lambda: expected_point_share(10**308, -(10**308), 1)),
    (
        "reliability rd",
        lambda: reliability(
            RatingState(rating=3.0, rd=1e200, rd_as_of=date(2020, 1, 1)),
            Config(),
            date(2026, 1, 1),
        ),
    ),
    (
        "prequential at 600",
        lambda: prequential_predictions(
            [
                Match(
                    "syn-m1",
                    ("syn-a",),
                    ("syn-b",),
                    ((600, 598),),
                    date(2026, 3, 1),
                    S,
                    game_format=GameFormat(RALLY, 600, 2),
                )
            ],
            cfg=_rally_600_config(),
            burn_in_matches=0,
        ),
    ),
]


@pytest.mark.parametrize(("label", "call"), OVERFLOWS, ids=[label for label, _ in OVERFLOWS])
def test_overflow_raises_evaluation_error(label: str, call: Callable[[], object]) -> None:
    with pytest.raises(EvaluationError, match="too large to compute"):
        call()


# -- N: Config.from_dict nested data ----------------------------------------------


class _Counted(str):
    calls = 0

    def __hash__(self) -> int:
        _Counted.calls += 1
        return str.__hash__(self)

    def __repr__(self) -> str:
        _Counted.calls += 1
        return "counted"

    def isdigit(self) -> bool:
        _Counted.calls += 1
        return True

    def isascii(self) -> bool:
        _Counted.calls += 1
        return True

    def __int__(self) -> int:
        _Counted.calls += 1
        return 11


class _Shown:
    calls = 0

    def __repr__(self) -> str:
        _Shown.calls += 1
        return "shown"


def _data() -> dict[str, object]:
    return json.loads(json.dumps(Config().to_dict()))  # type: ignore[no-any-return]


def _with_key(name: str, key: object) -> dict[str, object]:
    data = _data()
    inner = dict(data[name])  # type: ignore[call-overload]
    first = next(iter(inner))
    inner[key] = inner.pop(first)
    data[name] = inner
    return data


NESTED: list[tuple[str, Callable[[], dict[str, object]], str]] = [
    (
        "match_weights key",
        lambda: _with_key("match_weights", _Counted("tournament")),
        "match_weights",
    ),
    ("scale key", lambda: _with_key("scale_by_scoring", _Counted("rally")), "scale_by_scoring"),
    ("game length key", lambda: _with_key("game_length_weights", _Counted("abc")), "game_length"),
    (
        "allowed scoring",
        lambda: {**_data(), "allowed_game_formats": [[_Counted("rally"), 11]]},
        "allowed_game_formats",
    ),
    (
        "allowed entry object",
        lambda: {**_data(), "allowed_game_formats": [_Shown()]},
        "allowed_game_formats",
    ),
    (
        "allowed points object",
        lambda: {**_data(), "allowed_game_formats": [["rally", _Shown()]]},
        "allowed_game_formats",
    ),
    (
        "allowed entry list subclass",
        lambda: {**_data(), "allowed_game_formats": [type("_L", (list,), {})(["rally", 11])]},
        "allowed_game_formats",
    ),
]


@pytest.mark.parametrize(
    ("label", "make", "pattern"), NESTED, ids=[label for label, _, _ in NESTED]
)
def test_from_dict_nested_data_runs_no_caller_code(
    label: str, make: Callable[[], dict[str, object]], pattern: str
) -> None:
    data = make()
    _Counted.calls = 0
    _Shown.calls = 0
    with pytest.raises(ConfigError, match=pattern):
        Config.from_dict(data)
    assert _Counted.calls == 0
    assert _Shown.calls == 0


def test_from_dict_plain_nested_data_still_loads() -> None:
    assert Config.from_dict(_data()) == Config()
