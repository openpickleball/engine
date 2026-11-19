"""GameFormat rules check each field's type before its range (issue #88).

Through Match.validate a bad game format raises InvalidMatchError. Called any
other way (GameFormat.validate, the win probability functions, and
RatingEngine.predict_win_probability) it raises ConfigError with the same
message. All ids are synthetic.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pytest

from open_engine import (
    ConfigError,
    GameFormat,
    InvalidMatchError,
    Match,
    RatingEngine,
    RatingFormat,
    ScoringType,
    game_win_probability,
    match_win_probability,
    prequential_predictions,
    replay,
)

ON = date(2026, 1, 5)
BASE = Match("syn-m1", ("syn-a",), ("syn-b",), ((11, 5),), ON, RatingFormat.SINGLES)


class _Int(int):
    """An int subclass, rejected since issue #116: it could override comparison."""


BAD: list[Any] = [
    pytest.param(
        {"scoring": "rally"}, "scoring must be a ScoringType, not str", id="scoring-rally"
    ),
    pytest.param(
        {"scoring": "side_out"}, "scoring must be a ScoringType, not str", id="scoring-side_out"
    ),
    pytest.param(
        {"scoring": None}, "scoring must be a ScoringType, not NoneType", id="scoring-none"
    ),
    pytest.param({"points_to": "11"}, "points_to must be an int, not str", id="points_to-str"),
    pytest.param({"points_to": 11.0}, "points_to must be an int, not float", id="points_to-float"),
    pytest.param({"points_to": True}, "points_to must be an int, not bool", id="points_to-bool"),
    pytest.param(
        {"points_to": None}, "points_to must be an int, not NoneType", id="points_to-none"
    ),
    pytest.param({"win_by": "2"}, "win_by must be an int, not str", id="win_by-str"),
    pytest.param({"win_by": 2.0}, "win_by must be an int, not float", id="win_by-float"),
    pytest.param({"win_by": True}, "win_by must be an int, not bool", id="win_by-bool"),
    pytest.param({"cap": "15"}, "cap must be an int or None, not str", id="cap-str"),
    pytest.param({"cap": 15.0}, "cap must be an int or None, not float", id="cap-float"),
    pytest.param({"cap": True}, "cap must be an int or None, not bool", id="cap-bool"),
    pytest.param({"points_to": 0}, "points_to must be positive", id="points_to-zero"),
    pytest.param({"win_by": 0}, "win_by must be positive", id="win_by-zero"),
    pytest.param({"cap": 5}, "cap must be greater than points_to", id="cap-below"),
    pytest.param(
        {"points_to": _Int(11)}, "points_to must be an int, not _Int", id="points_to-int-subclass"
    ),
    pytest.param({"win_by": _Int(2)}, "win_by must be an int, not _Int", id="win_by-int-subclass"),
    pytest.param({"cap": _Int(15)}, "cap must be an int or None, not _Int", id="cap-int-subclass"),
]


def _row(changes: dict[str, Any]) -> Match:
    return dataclasses.replace(BASE, game_format=GameFormat(**changes))


@pytest.mark.parametrize(("changes", "message"), BAD)
def test_match_validate_raises_invalid_match_error(changes: dict[str, Any], message: str):
    with pytest.raises(InvalidMatchError, match=f"^{message}$"):
        _row(changes).validate()


@pytest.mark.parametrize(("changes", "message"), BAD)
def test_record_rejects_a_bad_game_format_and_changes_nothing(
    changes: dict[str, Any], message: str
):
    engine = RatingEngine()
    other = Match("syn-m0", ("syn-f",), ("syn-g",), ((11, 3),), ON, RatingFormat.SINGLES)
    assert engine.record(other).counted
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match=f"^{message}$"):
        engine.record(_row(changes))
    assert engine.to_snapshot() == before
    assert not {"syn-a", "syn-b"} & set(engine.players)


def _valid(i: int) -> Match:
    return Match(
        f"syn-v{i}",
        ("syn-p",),
        ("syn-q",),
        ((11, i % 9),),
        ON + timedelta(days=i),
        RatingFormat.SINGLES,
    )


AS_OF = ON + timedelta(days=10)
POSITIONS = ["first", "middle", "last", "after_as_of"]


def _ledger_with(bad: Match, position: str) -> list[Match]:
    rows = [_valid(i) for i in range(6)]
    if position == "after_as_of":
        return [*rows, dataclasses.replace(bad, played_on=AS_OF + timedelta(days=5))]
    if position == "first":
        return [bad, *rows]
    if position == "middle":
        return [*rows[:3], bad, *rows[3:]]
    return [*rows, bad]


@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize(("changes", "message"), BAD)
def test_replay_rejects_a_bad_game_format_wherever_it_sits(
    changes: dict[str, Any], message: str, position: str
):
    with pytest.raises(InvalidMatchError, match=f"^{message}$"):
        replay(_ledger_with(_row(changes), position), as_of=AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("changes", "message"), BAD)
def test_prequential_rejects_a_bad_game_format(changes: dict[str, Any], message: str, mode: Any):
    rows = [*(_valid(i) for i in range(6)), _row(changes)]
    with pytest.raises(InvalidMatchError, match=f"^{message}$"):
        prequential_predictions(rows, burn_in_matches=0, mode=mode)


def _direct(fmt: GameFormat) -> object:
    fmt.validate()
    return None


def _game(fmt: GameFormat) -> object:
    return game_win_probability(0.6, fmt)


def _match(fmt: GameFormat) -> object:
    return match_win_probability(0.6, fmt, 3)


def _engine(fmt: GameFormat) -> object:
    return RatingEngine().predict_win_probability(("syn-a",), ("syn-b",), RatingFormat.SINGLES, fmt)


@pytest.mark.parametrize(
    "call", [_direct, _game, _match, _engine], ids=["direct", "game", "match", "engine"]
)
@pytest.mark.parametrize(("changes", "message"), BAD)
def test_other_entry_points_raise_config_error(
    call: Callable[[GameFormat], object], changes: dict[str, Any], message: str
):
    with pytest.raises(ConfigError, match=f"^{message}$") as raised:
        call(GameFormat(**changes))
    assert not isinstance(raised.value, InvalidMatchError)


GOOD: list[Any] = [
    pytest.param({}, id="defaults"),
    pytest.param({"cap": None}, id="cap-none"),
    pytest.param({"scoring": ScoringType.RALLY}, id="scoring-member"),
]


@pytest.mark.parametrize("changes", GOOD)
def test_valid_game_formats_still_validate_and_record(changes: dict[str, Any]):
    fmt = GameFormat(**changes)
    fmt.validate()
    row = _row(changes)
    row.validate()
    assert RatingEngine().record(row).counted
    assert 0.0 < game_win_probability(0.6, fmt) < 1.0
