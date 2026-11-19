"""Every public entry point takes exact input types (issues #116, #118, #119).

A subclass, or an object whose __class__ only claims the type, raises the
entry point's OpenEngineError subclass before any state change. All ids
are synthetic.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from types import MappingProxyType
from zoneinfo import ZoneInfo

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
    MatchStatus,
    MatchType,
    OpenEngineError,
    Player,
    PlayerError,
    RatingEngine,
    RatingFormat,
    RatingState,
    ReferenceDateError,
    ScoringType,
    SnapshotError,
    calibrate_win_probability,
    evaluate,
    expected_point_share,
    fit_scale,
    fit_win_calibration,
    game_win_probability,
    ledger_order,
    match_win_probability,
    prequential_predictions,
    reliability,
    replay,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
DAY = date(2026, 3, 1)
LATER = date(2026, 6, 1)


class _Int(int):
    pass


class _Float(float):
    pass


class _Day(date):
    pass


class _Moment(datetime):
    pass


class _Str(str):
    pass


class _Tuple(tuple):
    pass


class _Dict(dict):
    pass


class _List(list):
    pass


class _Zone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "Z"


class _SubZone(ZoneInfo):
    pass


@dataclass(frozen=True)
class _Format(GameFormat):
    pass


class _State(RatingState):
    pass


def _claims(kind: type) -> object:
    """An object whose __class__ claims kind, so isinstance accepts it."""

    class Claims:
        @property  # type: ignore[misc]
        def __class__(self) -> type:
            return kind

        def __hash__(self) -> int:
            return 1

    return Claims()


def _match(match_id: str = "syn-m1", **changes: object) -> Match:
    fields: dict[str, object] = {
        "match_id": match_id,
        "team_a": ("syn-a", "syn-b"),
        "team_b": ("syn-c", "syn-d"),
        "games": ((11, 7),),
        "played_on": DAY,
        "rating_format": D,
    }
    fields.update(changes)
    return Match(**fields)  # type: ignore[arg-type]


def _at(zone: tzinfo, kind: type[datetime] = datetime) -> datetime:
    return kind(2026, 3, 1, 10, tzinfo=zone)


# (case id, Match field changes, message pattern). Each case breaks one
# field's type and nothing else.
BAD_MATCH_FIELDS: list[tuple[str, dict[str, object], str]] = [
    ("game_format subclass", {"game_format": _Format()}, "game_format must be"),
    ("game_format claims", {"game_format": _claims(GameFormat)}, "game_format must be"),
    (
        "scoring claims",
        {"game_format": GameFormat(scoring=_claims(ScoringType))},  # type: ignore[arg-type]
        "scoring must be",
    ),
    ("points_to subclass", {"game_format": GameFormat(points_to=_Int(11))}, "points_to must be"),
    ("win_by subclass", {"game_format": GameFormat(win_by=_Int(2))}, "win_by must be"),
    ("cap subclass", {"game_format": GameFormat(cap=_Int(15))}, "cap must be"),
    ("played_on subclass", {"played_on": _Day(2026, 3, 1)}, "played_on must be"),
    ("played_on claims", {"played_on": _claims(date)}, "played_on must be"),
    ("played_at subclass", {"played_at": _at(UTC, _Moment)}, "played_at must be"),
    ("played_at claims", {"played_at": _claims(datetime)}, "played_at must be"),
    (
        "played_at ZoneInfo subclass",
        {"played_at": _at(_SubZone("America/New_York"))},
        "played_at tzinfo must be",
    ),
    ("played_at tzinfo subclass", {"played_at": _at(_Zone())}, "played_at tzinfo must be"),
    ("sequence subclass", {"sequence": _Int(1)}, "sequence must be"),
    ("sequence claims", {"sequence": _claims(int)}, "sequence must be"),
    ("best_of subclass", {"best_of": _Int(1)}, "best_of must be"),
    ("best_of claims", {"best_of": _claims(int)}, "best_of must be"),
    ("status claims", {"status": _claims(MatchStatus)}, "status must be"),
    ("rating_format claims", {"rating_format": _claims(RatingFormat)}, "rating_format must be"),
    ("match_type claims", {"match_type": _claims(MatchType)}, "match_type must be"),
]


def _bad_ids() -> list[str]:
    return [case for case, _, _ in BAD_MATCH_FIELDS]


@pytest.mark.parametrize(("case", "changes", "pattern"), BAD_MATCH_FIELDS, ids=_bad_ids())
def test_match_validate_rejects(case: str, changes: dict[str, object], pattern: str) -> None:
    with pytest.raises(InvalidMatchError, match=pattern):
        Match.validate(_match(**changes))


@pytest.mark.parametrize(("case", "changes", "pattern"), BAD_MATCH_FIELDS, ids=_bad_ids())
def test_record_rejects_and_changes_nothing(
    case: str, changes: dict[str, object], pattern: str
) -> None:
    engine = RatingEngine()
    engine.record(_match("syn-m0"))
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match=pattern):
        engine.record(_match(**changes))
    assert engine.to_snapshot() == before


@pytest.mark.parametrize(("case", "changes", "pattern"), BAD_MATCH_FIELDS, ids=_bad_ids())
def test_replay_rejects(case: str, changes: dict[str, object], pattern: str) -> None:
    with pytest.raises(InvalidMatchError, match=pattern):
        replay([_match("syn-m0"), _match(**changes)], as_of=LATER)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("case", "changes", "pattern"), BAD_MATCH_FIELDS, ids=_bad_ids())
def test_prequential_rejects(
    case: str, changes: dict[str, object], pattern: str, mode: str
) -> None:
    with pytest.raises(InvalidMatchError, match=pattern):
        prequential_predictions([_match("syn-m0"), _match(**changes)], mode=mode)  # type: ignore[arg-type]


@pytest.mark.parametrize(("case", "changes", "pattern"), BAD_MATCH_FIELDS, ids=_bad_ids())
def test_ledger_order_rejects(case: str, changes: dict[str, object], pattern: str) -> None:
    with pytest.raises(InvalidMatchError, match=pattern):
        ledger_order(_match(**changes))


def test_issue_116_flip_format_reproduction() -> None:
    class FlipFormat(GameFormat):
        reads = 0

        @property  # type: ignore[override]
        def scoring(self) -> object:
            FlipFormat.reads += 1
            return ScoringType.SIDE_OUT if FlipFormat.reads < 3 else "bogus"

        @scoring.setter
        def scoring(self, value: object) -> None:
            pass

    engine = RatingEngine()
    before = engine.to_snapshot()
    match = _match(game_format=FlipFormat())
    with pytest.raises(InvalidMatchError, match="game_format must be"):
        engine.record(match)
    assert engine.to_snapshot() == before


def test_played_at_with_timezone_offset_is_accepted() -> None:
    zone = timezone(timedelta(hours=-5))
    result = RatingEngine().record(_match(played_at=_at(zone)))
    assert result.counted


# -- criterion 3: GameFormat with an extra instance attribute --------------------


def _format_with_extra() -> GameFormat:
    fmt = GameFormat()
    object.__setattr__(fmt, "validate", lambda: None)
    return fmt


def test_match_with_game_format_extra_attribute_rejected() -> None:
    engine = RatingEngine()
    with pytest.raises(InvalidMatchError, match="not GameFormat fields"):
        engine.record(_match(game_format=_format_with_extra()))
    assert engine.processed_match_ids == set()


@pytest.mark.parametrize(
    "call",
    [
        lambda fmt: GameFormat.validate(fmt),
        lambda fmt: GameFormat.validate_game(fmt, 11, 5),
        lambda fmt: game_win_probability(0.5, fmt),
        lambda fmt: match_win_probability(0.5, fmt, 1),
        lambda fmt: RatingEngine().predict_win_probability(("syn-a",), ("syn-b",), S, fmt),
    ],
    ids=["validate", "validate_game", "game_win", "match_win", "predict_win"],
)
def test_game_format_extra_attribute_config_error(call: Callable[[GameFormat], object]) -> None:
    with pytest.raises(ConfigError, match="not GameFormat fields"):
        call(_format_with_extra())


@pytest.mark.parametrize(
    "fmt",
    [_Format(), _claims(GameFormat)],
    ids=["subclass", "claims"],
)
@pytest.mark.parametrize(
    "call",
    [
        lambda fmt: GameFormat.validate(fmt),
        lambda fmt: GameFormat.validate_game(fmt, 11, 5),
        lambda fmt: game_win_probability(0.5, fmt),
        lambda fmt: match_win_probability(0.5, fmt, 1),
        lambda fmt: RatingEngine().predict_win_probability(("syn-a",), ("syn-b",), S, fmt),
    ],
    ids=["validate", "validate_game", "game_win", "match_win", "predict_win"],
)
def test_game_format_not_exact_config_error(call: Callable[[object], object], fmt: object) -> None:
    with pytest.raises(ConfigError, match="game_format must be"):
        call(fmt)


@pytest.mark.parametrize(
    ("score_a", "score_b"),
    [(_Int(11), 5), (11, _Int(5)), (True, 11), (11.0, 5), (_claims(int), 5)],
    ids=["a subclass", "b subclass", "bool", "float", "claims"],
)
def test_validate_game_scores_exact_int(score_a: object, score_b: object) -> None:
    with pytest.raises(InvalidMatchError, match="scores must be"):
        GameFormat.validate_game(GameFormat(), score_a, score_b)  # type: ignore[arg-type]


# -- criterion 5: the other arguments ---------------------------------------------


def _seeded_engine() -> RatingEngine:
    engine = RatingEngine()
    engine.seed_external_rating(
        "syn-a", D, ExternalRatingSnapshot("external_standard", 4.0, date(2026, 1, 1))
    )
    return engine


def _seed(**state_changes: object) -> Player:
    player = copy.deepcopy(_seeded_engine().players["syn-a"])
    state = player.ratings[D]
    for name, value in state_changes.items():
        setattr(state, name, value)
    return player


BAD_SEEDS: list[tuple[str, Callable[[], Player], str]] = [
    (
        "initial_source_date subclass",
        lambda: _seed(initial_source_date=_Day(2026, 1, 1)),
        "initial_source_date must be",
    ),
    ("rd_as_of subclass", lambda: _seed(rd_as_of=_Day(2026, 1, 1)), "rd_as_of must be"),
    (
        "ratings key claims",
        lambda: Player("syn-a", {_claims(RatingFormat): _seed().ratings[D]}),  # type: ignore[dict-item]
        "is not a RatingFormat",
    ),
]


@pytest.mark.parametrize(
    "use",
    [
        lambda seed: RatingEngine().add_player(seed),
        lambda seed: replay([], [seed], as_of=LATER),
        lambda seed: prequential_predictions([_match()], [seed]),
        lambda seed: prequential_predictions([_match()], [seed], mode="scheduled"),
    ],
    ids=["add_player", "replay", "prequential live", "prequential scheduled"],
)
@pytest.mark.parametrize(
    ("case", "make", "pattern"), BAD_SEEDS, ids=[case for case, _, _ in BAD_SEEDS]
)
def test_seed_rejects(
    use: Callable[[Player], object], case: str, make: Callable[[], Player], pattern: str
) -> None:
    with pytest.raises(InvalidSeedError, match=pattern):
        use(make())


def test_seed_external_rating_rejects_claimed_format() -> None:
    engine = RatingEngine()
    snap = ExternalRatingSnapshot("external_standard", 4.0, date(2026, 1, 1))
    with pytest.raises(InvalidSeedError, match="rating_format must be"):
        engine.seed_external_rating("syn-a", _claims(RatingFormat), snap)  # type: ignore[arg-type]
    assert engine.players == {}


def test_seed_external_rating_rejects_captured_on_subclass() -> None:
    engine = RatingEngine()
    snap = ExternalRatingSnapshot("external_standard", 4.0, _Day(2026, 1, 1))
    with pytest.raises(InvalidSeedError, match="captured_on must be"):
        engine.seed_external_rating("syn-a", D, snap)
    assert engine.players == {}


@pytest.mark.parametrize("flag", [1, 0, None, _claims(bool)], ids=["one", "zero", "none", "claims"])
def test_seed_external_rating_rejects_non_bool_replace(flag: object) -> None:
    engine = RatingEngine()
    snap = ExternalRatingSnapshot("external_standard", 4.0, date(2026, 1, 1))
    with pytest.raises(InvalidSeedError, match="replace_unplayed_seed must be"):
        engine.seed_external_rating("syn-a", D, snap, replace_unplayed_seed=flag)  # type: ignore[arg-type]
    assert engine.players == {}


def test_seed_provisional_rating_rejects_claimed_format() -> None:
    engine = RatingEngine()
    with pytest.raises(InvalidSeedError, match="rating_format must be"):
        engine.seed_provisional_rating("syn-a", _claims(RatingFormat))  # type: ignore[arg-type]
    assert engine.players == {}


def test_state_rejects_claimed_format() -> None:
    engine = _seeded_engine()
    with pytest.raises(PlayerError, match="rating_format must be"):
        engine.state("syn-a", _claims(RatingFormat))  # type: ignore[arg-type]


def test_player_summary_rejects_claimed_format() -> None:
    engine = _seeded_engine()
    with pytest.raises(PlayerError, match="rating_format must be"):
        engine.player_summary("syn-a", _claims(RatingFormat), as_of=LATER)  # type: ignore[arg-type]


@pytest.mark.parametrize("as_of", [_Day(2026, 6, 1), _claims(date)], ids=["subclass", "claims"])
def test_player_summary_rejects_as_of(as_of: object) -> None:
    engine = _seeded_engine()
    with pytest.raises(ReferenceDateError, match="as_of must be"):
        engine.player_summary("syn-a", D, as_of=as_of)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("args", "pattern"),
    [
        ((_Tuple(("syn-a",)), ("syn-b",), S), "team_a must be"),
        ((("syn-a",), _Tuple(("syn-b",)), S), "team_b must be"),
        (("syn-a", ("syn-b",), S), "team_a must be"),
        ((("syn-a",), ("syn-b",), _claims(RatingFormat)), "rating_format must be"),
        ((("syn-a",), ("syn-b",), S, _claims(ScoringType)), "scoring must be"),
    ],
    ids=["team_a subclass", "team_b subclass", "team_a str", "format claims", "scoring claims"],
)
def test_predict_point_share_rejects(args: tuple[object, ...], pattern: str) -> None:
    with pytest.raises(InvalidMatchError, match=pattern):
        RatingEngine().predict_point_share(*args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "best_of", [_Int(1), True, _claims(int)], ids=["subclass", "bool", "claims"]
)
def test_predict_win_probability_rejects_best_of(best_of: object) -> None:
    with pytest.raises(EvaluationError, match="best_of must be"):
        RatingEngine().predict_win_probability(
            ("syn-a",),
            ("syn-b",),
            S,
            GameFormat(),
            best_of,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("as_of", [_Day(2026, 6, 1), _claims(date)], ids=["subclass", "claims"])
def test_record_rejects_as_of(as_of: object) -> None:
    engine = RatingEngine()
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match="as_of must be"):
        engine.record(_match(), as_of=as_of)  # type: ignore[arg-type]
    assert engine.to_snapshot() == before


@pytest.mark.parametrize("as_of", [_Day(2026, 6, 1), _claims(date)], ids=["subclass", "claims"])
def test_replay_rejects_as_of(as_of: object) -> None:
    with pytest.raises(ReferenceDateError, match="as_of must be"):
        replay([_match()], as_of=as_of)  # type: ignore[arg-type]


def test_replay_rejects_claimed_whole_ledger() -> None:
    snap = replay([_match()], as_of=LATER).to_snapshot()
    with pytest.raises(SnapshotError, match="whole_ledger must be"):
        replay([_match()], as_of=LATER, from_snapshot=snap, whole_ledger=_claims(bool))  # type: ignore[arg-type]


def _small_ledger() -> list[Match]:
    rows = []
    for i in range(8):
        team_a, team_b = (("syn-a", "syn-b"), ("syn-c", "syn-d"))
        if i % 2:
            team_a, team_b = (("syn-a", "syn-c"), ("syn-b", "syn-d"))
        rows.append(
            _match(
                f"syn-m{i}",
                played_on=DAY + timedelta(days=i),
                team_a=team_a,
                team_b=team_b,
                games=((11, 5 + i % 4),),
            )
        )
    return rows


EVALUATION_CALLS: list[tuple[str, Callable[..., object]]] = [
    ("prequential live", lambda **kw: prequential_predictions(_small_ledger(), **kw)),
    (
        "prequential scheduled",
        lambda **kw: prequential_predictions(_small_ledger(), mode="scheduled", **kw),
    ),
    ("evaluate", lambda **kw: evaluate(_small_ledger(), **kw)),
    ("fit_scale", lambda **kw: fit_scale(_small_ledger(), **kw)),
    ("fit_win_calibration", lambda **kw: fit_win_calibration(_small_ledger(), **kw)),
]

BAD_EVALUATION_ARGS: list[tuple[str, dict[str, object], str]] = [
    ("score_from subclass", {"score_from": _Day(2026, 3, 1)}, "score_from must be"),
    ("score_from claims", {"score_from": _claims(date)}, "score_from must be"),
    ("anchor subclass", {"anchor": _Day(2026, 3, 1)}, "anchor must be"),
    ("scoring claims", {"scoring": _claims(ScoringType)}, "scoring must be"),
    ("scoring str", {"scoring": "side_out"}, "scoring must be"),
    ("burn_in subclass", {"burn_in_matches": _Int(1)}, "burn_in_matches must be"),
    ("burn_in bool", {"burn_in_matches": True}, "burn_in_matches must be"),
    ("burn_in float", {"burn_in_matches": 1.0}, "burn_in_matches must be"),
    ("burn_in claims", {"burn_in_matches": _claims(int)}, "burn_in_matches must be"),
]


@pytest.mark.parametrize(
    ("case", "kwargs", "pattern"),
    BAD_EVALUATION_ARGS,
    ids=[case for case, _, _ in BAD_EVALUATION_ARGS],
)
@pytest.mark.parametrize(
    ("name", "call"), EVALUATION_CALLS, ids=[name for name, _ in EVALUATION_CALLS]
)
def test_evaluation_rejects(
    name: str, call: Callable[..., object], case: str, kwargs: dict[str, object], pattern: str
) -> None:
    with pytest.raises(EvaluationError, match=pattern):
        call(**kwargs)


@pytest.mark.parametrize(
    ("name", "call"),
    [(name, call) for name, call in EVALUATION_CALLS if not name.startswith("prequential")]
    + [("prequential", lambda **kw: prequential_predictions(_small_ledger(), **kw))],
    ids=["evaluate", "fit_scale", "fit_win_calibration", "prequential"],
)
@pytest.mark.parametrize("mode", [_Str("live"), _claims(str)], ids=["subclass", "claims"])
def test_evaluation_rejects_mode(name: str, call: Callable[..., object], mode: object) -> None:
    with pytest.raises(EvaluationError, match="mode must be"):
        call(mode=mode)


@pytest.mark.parametrize("fit", [fit_scale, fit_win_calibration], ids=["scale", "win"])
@pytest.mark.parametrize("name", ["low", "high", "tolerance"])
@pytest.mark.parametrize(
    "make",
    [lambda v: _Float(v), lambda v: True, lambda v: _claims(float)],
    ids=["subclass", "bool", "claims"],
)
def test_fit_bounds_exact_number(
    fit: Callable[..., object], name: str, make: Callable[[float], object]
) -> None:
    defaults = {"low": 1.0, "high": 2.5, "tolerance": 0.01}
    with pytest.raises(EvaluationError, match=f"{name} must be an int or float"):
        fit(_small_ledger(), **{**defaults, name: make(defaults[name])})


def test_fit_scale_does_not_use_dataclasses_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    import open_engine.engine as engine_module

    original = engine_module.replace

    def guarded(obj: object, /, **changes: object) -> object:
        assert not isinstance(obj, Config), "fit_scale built a trial with dataclasses.replace"
        return original(obj, **changes)  # type: ignore[type-var]

    monkeypatch.setattr(engine_module, "replace", guarded)
    fit = fit_scale(_small_ledger(), burn_in_matches=1, tolerance=0.5)
    assert fit.scoring == "side_out"


# -- reliability, expected_point_share, and win probability ----------------------


def _state(**changes: object) -> RatingState:
    state = RatingState(rating=3.5, rd=0.5, rd_as_of=date(2026, 1, 1))
    for name, value in changes.items():
        setattr(state, name, value)
    return state


@pytest.mark.parametrize(
    ("make", "pattern"),
    [
        (lambda: _State(rating=3.5, rd=0.5), "state must be"),
        (lambda: _claims(RatingState), "state must be"),
        (lambda: _state(rd=_Float(0.5)), "rd must be"),
        (lambda: _state(rd=True), "rd must be"),
        (lambda: _state(rd="0.5"), "rd must be"),
        (lambda: _state(rd_as_of=_Day(2026, 1, 1)), "rd_as_of must be"),
        (lambda: _state(partners=_Dict()), "partners must"),
        (lambda: _state(partners={"syn-b": _Day(2026, 1, 1)}), "partners must"),
        (lambda: _state(opponents=_Dict()), "opponents must"),
        (lambda: _state(opponents={"syn-b": _claims(date)}), "opponents must"),
    ],
    ids=[
        "state subclass",
        "state claims",
        "rd subclass",
        "rd bool",
        "rd str",
        "rd_as_of subclass",
        "partners subclass",
        "partners date subclass",
        "opponents subclass",
        "opponents date claims",
    ],
)
def test_reliability_rejects_state(make: Callable[[], object], pattern: str) -> None:
    with pytest.raises(EvaluationError, match=pattern):
        reliability(make(), Config(), LATER)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "as_of", [_Day(2026, 6, 1), _claims(date), None], ids=["sub", "claims", "none"]
)
def test_reliability_rejects_as_of(as_of: object) -> None:
    with pytest.raises(ReferenceDateError, match="as_of must be"):
        reliability(_state(), Config(), as_of)  # type: ignore[arg-type]


def test_reliability_plain_unchanged() -> None:
    assert reliability(_state(), Config(), LATER) == reliability(
        RatingState(rating=3.5, rd=0.5, rd_as_of=date(2026, 1, 1)), Config(), LATER
    )


NUMBERS: list[tuple[str, Callable[[float], object]]] = [
    ("subclass", lambda v: _Float(v)),
    ("bool", lambda v: True),
    ("claims", lambda v: _claims(float)),
    ("str", lambda v: str(v)),
]


@pytest.mark.parametrize(("make_case", "make"), NUMBERS, ids=[case for case, _ in NUMBERS])
@pytest.mark.parametrize(
    ("call", "args", "position", "pattern"),
    [
        (expected_point_share, (3.5, 3.0, 2.9), 0, "team_a_rating must be an int or float"),
        (expected_point_share, (3.5, 3.0, 2.9), 1, "team_b_rating must be an int or float"),
        (expected_point_share, (3.5, 3.0, 2.9), 2, "scale must be an int or float"),
        (game_win_probability, (0.5, GameFormat()), 0, "p must be an int or float"),
        (match_win_probability, (0.5, GameFormat(), 1), 0, "p must be an int or float"),
        (calibrate_win_probability, (0.5, 1.0), 0, "p must be an int or float"),
        (calibrate_win_probability, (0.5, 1.0), 1, "a must be an int or float"),
    ],
    ids=["share a", "share b", "share scale", "game p", "match p", "calibrate p", "calibrate a"],
)
def test_math_functions_reject_numbers(
    call: Callable[..., object],
    args: tuple[object, ...],
    position: int,
    pattern: str,
    make_case: str,
    make: Callable[[float], object],
) -> None:
    bad = list(args)
    bad[position] = make(args[position])  # type: ignore[arg-type]
    with pytest.raises(EvaluationError, match=pattern):
        call(*bad)


@pytest.mark.parametrize(
    "best_of", [_Int(1), True, _claims(int)], ids=["subclass", "bool", "claims"]
)
def test_match_win_probability_rejects_best_of(best_of: object) -> None:
    with pytest.raises(EvaluationError, match="best_of must be"):
        match_win_probability(0.5, GameFormat(), best_of)  # type: ignore[arg-type]


def test_math_functions_accept_plain_int() -> None:
    assert expected_point_share(3, 3, 3) == 0.5
    assert calibrate_win_probability(0.5, 1) == 0.5
    assert game_win_probability(0.5, GameFormat()) == pytest.approx(0.5)


# -- Config bool fields and from_dict --------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["self_reported_requires_both_teams", "reject_content_duplicates", "category_feeds_parent"],
)
def test_config_bool_fields_exact(name: str) -> None:
    with pytest.raises(ConfigError, match=f"{name} must be"):
        Config(**{name: _claims(bool)})  # type: ignore[arg-type]


@pytest.mark.parametrize("wrap", [MappingProxyType, _Dict], ids=["mapping proxy", "dict subclass"])
def test_config_from_dict_exact_dict(wrap: Callable[[dict[str, object]], object]) -> None:
    with pytest.raises(ConfigError, match="Config data must be"):
        Config.from_dict(wrap(Config().to_dict()))  # type: ignore[arg-type]


def test_config_from_dict_plain_round_trip() -> None:
    assert Config.from_dict(json.loads(json.dumps(Config().to_dict()))) == Config()


# -- snapshot containers ---------------------------------------------------------


def _snapshot() -> dict[str, object]:
    rows = [
        _match("syn-m1"),
        _match(
            "syn-m2",
            match_type=MatchType.SELF_REPORTED,
            confirmed_by=frozenset({"syn-a", "syn-c"}),
            played_on=date(2026, 3, 2),
        ),
    ]
    snap = replay(rows, as_of=LATER).to_snapshot()
    assert snap["resumable"] is True
    assert snap["self_reported_log"]
    return json.loads(json.dumps(snap))  # type: ignore[no-any-return]


def _edit(path: tuple[object, ...], wrap: Callable[[object], object]) -> dict[str, object]:
    snap = _snapshot()
    holder: object = snap
    for key in path[:-1]:
        holder = holder[key]  # type: ignore[index]
    holder[path[-1]] = wrap(holder[path[-1]])  # type: ignore[index]
    return snap


SNAPSHOT_EDITS: list[tuple[str, Callable[[], object], str]] = [
    ("top proxy", lambda: MappingProxyType(_snapshot()), "snapshot must be an object"),
    ("top dict subclass", lambda: _Dict(_snapshot()), "snapshot must be an object"),
    ("players list subclass", lambda: _edit(("players",), _List), "players must be a list"),
    (
        "player dict subclass",
        lambda: _edit(("players", 0), _Dict),
        r"players\[0\] must be an object",
    ),
    (
        "player ratings subclass",
        lambda: _edit(("players", 0, "ratings"), _Dict),
        "ratings must be an object",
    ),
    (
        "player external_ids subclass",
        lambda: _edit(("players", 0, "external_ids"), _Dict),
        "external_ids must",
    ),
    (
        "state partners subclass",
        lambda: _edit(("players", 0, "ratings", "doubles", "partners"), _Dict),
        "partners must be an object",
    ),
    (
        "processed ids subclass",
        lambda: _edit(("processed_match_ids",), _List),
        "processed_match_ids must be a list",
    ),
    (
        "log entry subclass",
        lambda: _edit(("self_reported_log", 0), _Dict),
        r"self_reported_log\[0\] must be an object",
    ),
    (
        "log teams subclass",
        lambda: _edit(("self_reported_log", 0, "teams"), _List),
        "teams must be a list",
    ),
    (
        "log team subclass",
        lambda: _edit(("self_reported_log", 0, "teams", 0), _List),
        r"teams\[0\] must be a team",
    ),
    (
        "log dates subclass",
        lambda: _edit(("self_reported_log", 0, "dates"), _List),
        "dates must be a non-empty list",
    ),
    (
        "content_keys subclass",
        lambda: _edit(("content_keys",), _Dict),
        "content_keys must be an object",
    ),
    (
        "possible_duplicates subclass",
        lambda: _edit(("possible_duplicates",), _Dict),
        "possible_duplicates must be an object",
    ),
    (
        "ledger_digests subclass",
        lambda: _edit(("ledger_digests",), _Dict),
        "ledger_digests must be an object",
    ),
    (
        "seed_sensitive subclass",
        lambda: _edit(("seed_sensitive_rejections",), _Dict),
        "seed_sensitive_rejections must be an object",
    ),
    (
        "replay_info subclass",
        lambda: _edit(("replay_info",), _Dict),
        "replay_info must be an object",
    ),
    ("resumable claims", lambda: _edit(("resumable",), lambda v: _claims(bool)), "resumable"),
]


@pytest.mark.parametrize(
    ("case", "make", "pattern"), SNAPSHOT_EDITS, ids=[case for case, _, _ in SNAPSHOT_EDITS]
)
def test_from_snapshot_exact_containers(
    case: str, make: Callable[[], object], pattern: str
) -> None:
    with pytest.raises(SnapshotError, match=pattern):
        RatingEngine.from_snapshot(make())  # type: ignore[arg-type]


def test_from_snapshot_plain_round_trip() -> None:
    snap = _snapshot()
    assert RatingEngine.from_snapshot(snap).to_snapshot() == snap


def test_every_error_is_an_open_engine_error() -> None:
    for kind in (
        ConfigError,
        EvaluationError,
        InvalidMatchError,
        InvalidSeedError,
        PlayerError,
        ReferenceDateError,
        SnapshotError,
    ):
        assert issubclass(kind, OpenEngineError)


# -- criterion 4: an exact Config at every entry point ---------------------------


class _Big(int):
    def __mul__(self, other: object) -> int:
        return 10**6

    __rmul__ = __mul__


@dataclass(frozen=True, eq=False)
class _Evil(Config):
    def __post_init__(self) -> None:
        super().__post_init__()
        weights = dict(self.match_weights)
        weights[MatchType.TOURNAMENT] = _Big(1)
        object.__setattr__(self, "match_weights", weights)


def _with_attribute(name: str, value: object) -> Config:
    cfg = Config()
    object.__setattr__(cfg, name, value)
    return cfg


BAD_CONFIGS: list[tuple[str, Callable[[], object], str]] = [
    ("subclass", _Evil, "cfg must be exactly a Config"),
    ("other object", object, "cfg must be exactly a Config"),
    ("falsy object", lambda: 0, "cfg must be exactly a Config"),
    ("claims", lambda: _claims(Config), "cfg must be exactly a Config"),
    ("extra attribute", lambda: _with_attribute("extra", 1), "not Config fields"),
    ("fields attribute", lambda: _with_attribute("__dataclass_fields__", {}), "not Config fields"),
    (
        "fingerprint attribute",
        lambda: _with_attribute("fingerprint", lambda: Config().fingerprint()),
        "not Config fields",
    ),
]


def _plain_snapshot() -> dict[str, object]:
    return replay([_match()], as_of=LATER).to_snapshot()


CONFIG_CALLS: list[tuple[str, Callable[[object], object]]] = [
    ("RatingEngine", lambda cfg: RatingEngine(cfg)),  # type: ignore[arg-type]
    ("from_snapshot", lambda cfg: RatingEngine.from_snapshot(_plain_snapshot(), cfg)),  # type: ignore[arg-type]
    ("replay", lambda cfg: replay([_match()], cfg=cfg, as_of=LATER)),  # type: ignore[arg-type]
    (
        "resume",
        lambda cfg: replay([_match()], cfg=cfg, as_of=LATER, from_snapshot=_plain_snapshot()),  # type: ignore[arg-type]
    ),
    ("prequential live", lambda cfg: prequential_predictions(_small_ledger(), cfg=cfg)),  # type: ignore[arg-type]
    (
        "prequential scheduled",
        lambda cfg: prequential_predictions(_small_ledger(), cfg=cfg, mode="scheduled"),  # type: ignore[arg-type]
    ),
    ("evaluate", lambda cfg: evaluate(_small_ledger(), cfg=cfg)),  # type: ignore[arg-type]
    ("fit_scale", lambda cfg: fit_scale(_small_ledger(), cfg=cfg)),  # type: ignore[arg-type]
    ("fit_win_calibration", lambda cfg: fit_win_calibration(_small_ledger(), cfg=cfg)),  # type: ignore[arg-type]
    ("reliability", lambda cfg: reliability(_state(), cfg, LATER)),  # type: ignore[arg-type]
]


@pytest.mark.parametrize(
    ("case", "make", "pattern"), BAD_CONFIGS, ids=[case for case, _, _ in BAD_CONFIGS]
)
@pytest.mark.parametrize(("name", "call"), CONFIG_CALLS, ids=[name for name, _ in CONFIG_CALLS])
def test_config_entry_points_reject(
    name: str, call: Callable[[object], object], case: str, make: Callable[[], object], pattern: str
) -> None:
    with pytest.raises(ConfigError, match=pattern):
        call(make())


@pytest.mark.parametrize(("name", "call"), CONFIG_CALLS, ids=[name for name, _ in CONFIG_CALLS])
def test_config_entry_points_accept_none(name: str, call: Callable[[object], object]) -> None:
    if name == "reliability":
        with pytest.raises(ConfigError, match="cfg must be exactly a Config"):
            call(None)
        return
    call(None)


def test_issue_118_evil_reproduction() -> None:
    evil = _Evil()
    with pytest.raises(ConfigError, match="cfg must be exactly a Config"):
        RatingEngine(evil)
    plain = RatingEngine(Config()).record(_match(match_type=MatchType.TOURNAMENT))
    assert plain.deltas == RatingEngine().record(_match(match_type=MatchType.TOURNAMENT)).deltas
