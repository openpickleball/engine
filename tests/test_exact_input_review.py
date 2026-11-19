"""Review findings on the exact input type rule (issues #116, #118, #119).

A timedelta subclass inside an exact timezone, an object of an enum's own
type that is not a member, a GameFormat checked only for shape, dict keys
of a str subclass, numbers too large for a float, a best_of checked after
prediction work, reliability's interaction keys, and messages that run a
caller's __repr__. All ids are synthetic.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone

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


# -- A: a timedelta subclass inside an exact timezone -----------------------------


class _RewritingDelta(timedelta):
    calls = 0
    target: Match | None = None

    def total_seconds(self) -> float:
        _RewritingDelta.calls += 1
        if _RewritingDelta.target is not None:
            object.__setattr__(_RewritingDelta.target, "games", ((7, 11),))
        return timedelta.total_seconds(self)


class _EqualDelta(timedelta):
    calls = 0

    def __eq__(self, other: object) -> bool:
        _EqualDelta.calls += 1
        return True

    def __ne__(self, other: object) -> bool:
        _EqualDelta.calls += 1
        return False

    __hash__ = timedelta.__hash__


ENTRY_POINTS: list[tuple[str, Callable[[RatingEngine, list[Match]], object]]] = [
    ("validate", lambda engine, rows: [Match.validate(row) for row in rows]),
    ("ledger_order", lambda engine, rows: [ledger_order(row) for row in rows]),
    ("record", lambda engine, rows: [engine.record(row) for row in rows]),
    ("replay", lambda engine, rows: replay(rows, as_of=LATER)),
    ("prequential live", lambda engine, rows: prequential_predictions(rows)),
    ("prequential scheduled", lambda engine, rows: prequential_predictions(rows, mode="scheduled")),
]


@pytest.mark.parametrize(("name", "call"), ENTRY_POINTS, ids=[name for name, _ in ENTRY_POINTS])
def test_offset_subclass_cannot_rewrite_the_match(
    name: str, call: Callable[[RatingEngine, list[Match]], object]
) -> None:
    zone = timezone(_RewritingDelta(hours=-5))
    row = _match(played_at=datetime(2026, 3, 1, 10, tzinfo=zone))
    _RewritingDelta.target = row
    _RewritingDelta.calls = 0
    engine = RatingEngine()
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match="played_at offset must be"):
        call(engine, [row])
    assert _RewritingDelta.calls == 0
    assert row.games == ((11, 7),)
    assert engine.to_snapshot() == before
    _RewritingDelta.target = None


@pytest.mark.parametrize(("name", "call"), ENTRY_POINTS, ids=[name for name, _ in ENTRY_POINTS])
def test_offset_subclass_cannot_collapse_conflicting_rows(
    name: str, call: Callable[[RatingEngine, list[Match]], object]
) -> None:
    zone = timezone(_EqualDelta(hours=-5))
    at = datetime(2026, 3, 1, 10, tzinfo=zone)
    rows = [_match(played_at=at), _match(played_at=at, games=((3, 11),))]
    _EqualDelta.calls = 0
    engine = RatingEngine()
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match="played_at offset must be"):
        call(engine, rows)
    assert _EqualDelta.calls == 0
    assert engine.to_snapshot() == before


# -- B: an object of an enum's own type that is not a member ----------------------


def _fake(kind: type, value: str) -> object:
    fake = str.__new__(kind, value)
    fake._value_ = value  # type: ignore[attr-defined]
    fake._name_ = value.upper()  # type: ignore[attr-defined]
    return fake


FAKE_SINGLES = _fake(RatingFormat, "singles")
FAKE_SIDE_OUT = _fake(ScoringType, "side_out")

BAD_MATCH_ENUMS: list[tuple[str, dict[str, object], str]] = [
    ("status", {"status": _fake(MatchStatus, "completed"), "games": ()}, "status must be"),
    ("rating_format", {"rating_format": _fake(RatingFormat, "doubles")}, "rating_format must be"),
    ("match_type", {"match_type": _fake(MatchType, "tournament")}, "match_type must be"),
    (
        "scoring",
        {"game_format": GameFormat(scoring=FAKE_SIDE_OUT)},  # type: ignore[arg-type]
        "scoring must be",
    ),
]


@pytest.mark.parametrize(
    ("case", "changes", "pattern"), BAD_MATCH_ENUMS, ids=[case for case, _, _ in BAD_MATCH_ENUMS]
)
@pytest.mark.parametrize(("name", "call"), ENTRY_POINTS, ids=[name for name, _ in ENTRY_POINTS])
def test_match_rejects_enum_non_member(
    name: str,
    call: Callable[[RatingEngine, list[Match]], object],
    case: str,
    changes: dict[str, object],
    pattern: str,
) -> None:
    engine = RatingEngine()
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match=pattern):
        call(engine, [_match(**changes)])
    assert engine.to_snapshot() == before


def test_game_format_validate_rejects_enum_non_member() -> None:
    with pytest.raises(ConfigError, match="scoring must be"):
        GameFormat.validate(GameFormat(scoring=FAKE_SIDE_OUT))  # type: ignore[arg-type]


def test_seed_methods_reject_format_non_member() -> None:
    engine = RatingEngine()
    snap = ExternalRatingSnapshot("external_standard", 4.0, date(2026, 1, 1))
    with pytest.raises(InvalidSeedError, match="rating_format must be"):
        engine.seed_provisional_rating("syn-a", FAKE_SINGLES)  # type: ignore[arg-type]
    with pytest.raises(InvalidSeedError, match="rating_format must be"):
        engine.seed_external_rating("syn-a", FAKE_SINGLES, snap)  # type: ignore[arg-type]
    assert engine.players == {}
    engine.to_snapshot()


def _seed() -> Player:
    engine = RatingEngine()
    engine.seed_external_rating(
        "syn-a", D, ExternalRatingSnapshot("external_standard", 4.0, date(2026, 1, 1))
    )
    return copy.deepcopy(engine.players["syn-a"])


@pytest.mark.parametrize(
    "use",
    [
        lambda seed: RatingEngine().add_player(seed),
        lambda seed: replay([], [seed], as_of=LATER),
        lambda seed: prequential_predictions([_match()], [seed]),
    ],
    ids=["add_player", "replay", "prequential"],
)
def test_seed_rejects_ratings_key_non_member(use: Callable[[Player], object]) -> None:
    seed = _seed()
    seed.ratings = {FAKE_SINGLES: seed.ratings[D]}  # type: ignore[dict-item]
    with pytest.raises(InvalidSeedError, match="ratings key"):
        use(seed)


def test_lookups_reject_format_non_member() -> None:
    engine = RatingEngine()
    engine.seed_provisional_rating("syn-a", S)
    with pytest.raises(PlayerError, match="rating_format must be"):
        engine.state("syn-a", FAKE_SINGLES)  # type: ignore[arg-type]
    with pytest.raises(PlayerError, match="rating_format must be"):
        engine.player_summary("syn-a", FAKE_SINGLES, as_of=LATER)  # type: ignore[arg-type]


def test_predict_point_share_rejects_enum_non_members() -> None:
    engine = RatingEngine()
    with pytest.raises(InvalidMatchError, match="rating_format must be"):
        engine.predict_point_share(("syn-a", "syn-b"), ("syn-c", "syn-d"), FAKE_SINGLES)  # type: ignore[arg-type]
    with pytest.raises(InvalidMatchError, match="scoring must be"):
        engine.predict_point_share(("syn-a",), ("syn-b",), S, FAKE_SIDE_OUT)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "call",
    [
        lambda: prequential_predictions([_match()], scoring=FAKE_SIDE_OUT),
        lambda: evaluate([_match()], scoring=FAKE_SIDE_OUT),
        lambda: fit_scale([_match()], scoring=FAKE_SIDE_OUT),
    ],
    ids=["prequential", "evaluate", "fit_scale"],
)
def test_evaluation_rejects_scoring_non_member(call: Callable[[], object]) -> None:
    with pytest.raises(EvaluationError, match="scoring must be"):
        call()


def _scales(key: object) -> dict[object, float]:
    return {key: 3.0, ScoringType.RALLY: 3.0}


@pytest.mark.parametrize(
    "build",
    [
        lambda: Config(scale_by_scoring=_scales(FAKE_SIDE_OUT)),  # type: ignore[arg-type]
        lambda: Config(win_calibration_by_scoring=_scales(FAKE_SIDE_OUT)),  # type: ignore[arg-type]
        lambda: Config(
            match_weights={
                **{t: 1.0 for t in MatchType if t is not MatchType.TOURNAMENT},
                _fake(MatchType, "tournament"): 1.0,
            }
        ),
        lambda: Config(
            allowed_game_formats=frozenset(
                {(FAKE_SIDE_OUT, 11), (ScoringType.RALLY, 11)}  # type: ignore[arg-type]
            )
        ),
    ],
    ids=["scale_by_scoring", "win_calibration_by_scoring", "match_weights", "allowed"],
)
def test_config_rejects_enum_non_member_keys(build: Callable[[], object]) -> None:
    with pytest.raises(ConfigError):
        build()


def test_config_still_converts_plain_str_keys() -> None:
    cfg = Config(scale_by_scoring={"side_out": 2.9, "rally": 2.9})  # type: ignore[dict-item]
    assert all(type(key) is ScoringType for key in cfg.scale_by_scoring)
    cfg.fingerprint()


# -- C: validate_game checks the format's field types -----------------------------


class _Int(int):
    calls = 0

    def __lt__(self, other: object) -> bool:
        _Int.calls += 1
        return int.__lt__(self, other)  # type: ignore[operator]


@pytest.mark.parametrize(
    "fmt",
    [GameFormat(points_to=_Int(11)), GameFormat(points_to="11")],  # type: ignore[arg-type]
    ids=["int subclass", "str"],
)
def test_validate_game_checks_field_types(fmt: GameFormat) -> None:
    _Int.calls = 0
    with pytest.raises(ConfigError, match="points_to must be"):
        GameFormat.validate_game(fmt, 11, 5)
    assert _Int.calls == 0


# -- D: dict keys of a str subclass -----------------------------------------------


class _Key(str):
    calls = 0

    def __eq__(self, other: object) -> bool:
        _Key.calls += 1
        return str.__eq__(self, other)  # type: ignore[arg-type]

    def __hash__(self) -> int:
        _Key.calls += 1
        return str.__hash__(self)


def _snapshot() -> dict[str, object]:
    snap = replay([_match()], as_of=LATER).to_snapshot()
    return json.loads(json.dumps(snap))  # type: ignore[no-any-return]


def _rekey(holder: dict[str, object], name: str) -> None:
    value = holder.pop(name)
    holder[_Key(name)] = value


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ((), "players"),
        (("players", 0), "id"),
        (("replay_info",), "as_of"),
        (("replay_info", "net_rating_created"), "doubles"),
    ],
    ids=["players", "player id", "replay_info as_of", "net format"],
)
def test_snapshot_rejects_str_subclass_keys(path: tuple[object, ...], name: str) -> None:
    snap = _snapshot()
    holder: object = snap
    for step in path:
        holder = holder[step]  # type: ignore[index]
    assert isinstance(holder, dict)
    _rekey(holder, name)
    _Key.calls = 0
    with pytest.raises(SnapshotError, match="key that is not a str"):
        RatingEngine.from_snapshot(snap)
    assert _Key.calls == 0


def test_snapshot_rejects_str_subclass_version_key() -> None:
    snap = _snapshot()
    _rekey(snap, "snapshot_version")
    _Key.calls = 0
    with pytest.raises(SnapshotError, match="key that is not a str"):
        RatingEngine.from_snapshot(snap)
    assert _Key.calls == 0


def test_from_dict_rejects_str_subclass_keys() -> None:
    data = Config().to_dict()
    _rekey(data, "k_min")
    _Key.calls = 0
    with pytest.raises(ConfigError, match="Config data has unknown keys"):
        Config.from_dict(data)
    assert _Key.calls == 0


def test_seed_ratings_key_is_not_hashed_before_its_check() -> None:
    seed = _seed()
    seed.ratings = {_Key("doubles"): seed.ratings[D]}  # type: ignore[dict-item]
    _Key.calls = 0
    with pytest.raises(InvalidSeedError, match="ratings key"):
        RatingEngine().add_player(seed)
    assert _Key.calls == 0


# -- E: numbers too large for a float ---------------------------------------------

HUGE = 10**400


@pytest.mark.parametrize(
    ("call", "pattern"),
    [
        (lambda: fit_scale([], low=1, high=HUGE), "high must be"),
        (lambda: fit_scale([], low=-HUGE, high=1), "low must be"),
        (lambda: fit_scale([], tolerance=HUGE), "tolerance must be"),
        (lambda: expected_point_share(HUGE, 0, 1), "team_a_rating must be"),
        (lambda: expected_point_share(0, 0, HUGE), "scale must be"),
        (lambda: calibrate_win_probability(0.7, HUGE), "a must be"),
        (lambda: calibrate_win_probability(HUGE, 1.0), "p must be"),
        (lambda: game_win_probability(HUGE, GameFormat()), "p must be"),
        (lambda: match_win_probability(HUGE, GameFormat(), 1), "p must be"),
        (lambda: match_win_probability(0.6, GameFormat(), 2001), "best_of"),
        (lambda: match_win_probability(0.6, GameFormat(), HUGE + 1), "best_of"),
        (
            lambda: reliability(RatingState(rating=3.5, rd=HUGE), Config(), LATER),
            "rd must be",
        ),
    ],
    ids=[
        "fit high",
        "fit low",
        "fit tolerance",
        "share rating",
        "share scale",
        "calibrate a",
        "calibrate p",
        "game p",
        "match p",
        "match best_of 2001",
        "match best_of huge",
        "reliability rd",
    ],
)
def test_huge_numbers_raise_evaluation_error(call: Callable[[], object], pattern: str) -> None:
    with pytest.raises(EvaluationError, match=pattern):
        call()


# -- F: predict_win_probability checks best_of first ------------------------------


@pytest.mark.parametrize("best_of", [_Int(3), 2, True], ids=["subclass", "even", "bool"])
def test_predict_win_probability_checks_best_of_before_prediction(best_of: object) -> None:
    engine = RatingEngine()
    with pytest.raises(EvaluationError, match="best_of must be"):
        engine.predict_win_probability(
            ("syn-a",),
            ("syn-a",),
            S,
            GameFormat(),
            best_of,  # type: ignore[arg-type]
        )


# -- I: reliability's interaction keys --------------------------------------------


class _Str(str):
    pass


@pytest.mark.parametrize("key", [1, _Str("syn-b"), None], ids=["int", "str subclass", "none"])
@pytest.mark.parametrize("name", ["partners", "opponents"])
def test_reliability_rejects_interaction_keys(name: str, key: object) -> None:
    state = RatingState(rating=3.5, rd=0.5, rd_as_of=date(2026, 1, 1))
    setattr(state, name, {key: date(2026, 5, 1)})
    with pytest.raises(EvaluationError, match=f"{name} must"):
        reliability(state, Config(), LATER)


# -- J: messages never run a caller's __repr__ ------------------------------------


class _LoudStr(str):
    def __repr__(self) -> str:
        raise RuntimeError("repr ran")


class _LoudInt(int):
    def __repr__(self) -> str:
        raise RuntimeError("repr ran")


class _LoudKey:
    def __repr__(self) -> str:
        raise RuntimeError("repr ran")


def test_mode_message_names_the_type() -> None:
    with pytest.raises(EvaluationError, match="mode must be .*not _LoudStr"):
        prequential_predictions([_match()], mode=_LoudStr("live"))  # type: ignore[arg-type]


def test_best_of_message_names_the_type() -> None:
    with pytest.raises(EvaluationError, match="best_of must be .*_LoudInt"):
        match_win_probability(0.5, GameFormat(), _LoudInt(3))


def test_seed_ratings_key_message_names_the_type() -> None:
    seed = _seed()
    seed.ratings = {_LoudKey(): seed.ratings[D]}  # type: ignore[dict-item]
    with pytest.raises(InvalidSeedError, match="ratings key .*_LoudKey"):
        RatingEngine().add_player(seed)


class _Day(date):
    pass


def test_player_summary_as_of_message_names_the_type() -> None:
    engine = RatingEngine()
    engine.seed_provisional_rating("syn-a", S)
    with pytest.raises(ReferenceDateError, match="exactly a date, not _Day"):
        engine.player_summary("syn-a", S, as_of=_Day(2026, 6, 1))


def test_replay_as_of_message_names_the_type() -> None:
    with pytest.raises(ReferenceDateError, match="exactly a date, not _Day"):
        replay([], as_of=_Day(2026, 6, 1))
