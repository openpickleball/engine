"""Match.validate checks the type of each field before it uses it (issues #65 and #83).

A field of the wrong type raises InvalidMatchError naming the field, never a
TypeError, AttributeError, or KeyError, and is never accepted. A str equal to
an enum member's value is rejected. All ids are synthetic.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from open_engine import (
    GameFormat,
    InvalidMatchError,
    Match,
    MatchStatus,
    MatchType,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)

ON = date(2026, 1, 5)
BASE = Match("syn-m1", ("syn-b", "syn-c"), ("syn-d", "syn-e"), ((11, 5),), ON, RatingFormat.DOUBLES)


class _Int(int):
    """An int subclass, accepted like int."""


BAD: list[Any] = [
    pytest.param("sequence", "1", "sequence must be an int, not str", id="sequence-str"),
    pytest.param("sequence", True, "sequence must be an int, not bool", id="sequence-bool"),
    pytest.param("sequence", 1.5, "sequence must be an int, not float", id="sequence-float"),
    pytest.param("sequence", 1.0, "sequence must be an int, not float", id="sequence-float-int"),
    pytest.param("best_of", "3", "best_of must be an int, not str", id="best_of-str"),
    pytest.param("best_of", True, "best_of must be an int, not bool", id="best_of-bool"),
    pytest.param("best_of", 1.0, "best_of must be an int, not float", id="best_of-float"),
    pytest.param(
        "rating_format",
        "doubles",
        "rating_format must be a RatingFormat, not str",
        id="rating_format-str-value",
    ),
    pytest.param(
        "match_type",
        "tournament",
        "match_type must be a MatchType, not str",
        id="match_type-str-tournament",
    ),
    pytest.param(
        "match_type",
        "self_reported",
        "match_type must be a MatchType, not str",
        id="match_type-str-self_reported",
    ),
    pytest.param(
        "match_type", "bogus", "match_type must be a MatchType, not str", id="match_type-bogus"
    ),
    pytest.param(
        "team_a", ["syn-b", "syn-c"], "team_a must be a tuple, not list", id="team_a-list"
    ),
    pytest.param(
        "team_b", ["syn-d", "syn-e"], "team_b must be a tuple, not list", id="team_b-list"
    ),
    pytest.param(
        "confirmed_by",
        "syn-b",
        "confirmed_by must be a set or frozenset, not str",
        id="confirmed_by-str",
    ),
    pytest.param(
        "confirmed_by",
        ["syn-b"],
        "confirmed_by must be a set or frozenset, not list",
        id="confirmed_by-list",
    ),
    pytest.param(
        "game_format", "x", "game_format must be a GameFormat, not str", id="game_format-str"
    ),
    pytest.param(
        "played_at",
        "2026-01-05",
        "played_at must be a datetime or None, not str",
        id="played_at-str",
    ),
    pytest.param(
        "played_at",
        ON,
        "played_at must be a datetime or None, not date",
        id="played_at-date",
    ),
]


def _bad(field: str, value: object, **changes: Any) -> Match:
    return dataclasses.replace(BASE, **{field: value, **changes})


@pytest.mark.parametrize(("field", "value", "message"), BAD)
def test_validate_rejects_a_field_of_the_wrong_type(field: str, value: object, message: str):
    with pytest.raises(InvalidMatchError, match=f"^{message}$"):
        _bad(field, value).validate()


@pytest.mark.parametrize(("field", "value", "message"), BAD)
def test_record_rejects_a_field_of_the_wrong_type_and_changes_nothing(
    field: str, value: object, message: str
):
    engine = RatingEngine()
    other = Match("syn-m0", ("syn-f",), ("syn-g",), ((11, 3),), ON, RatingFormat.SINGLES)
    assert engine.record(other).counted
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match=f"^{message}$"):
        engine.record(_bad(field, value))
    assert engine.to_snapshot() == before
    assert not {"syn-b", "syn-c", "syn-d", "syn-e"} & set(engine.players)


def _valid(i: int) -> Match:
    return Match(
        f"syn-v{i}",
        ("syn-p", "syn-q"),
        ("syn-r", "syn-s"),
        ((11, i % 9),),
        ON + timedelta(days=i),
        RatingFormat.DOUBLES,
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
@pytest.mark.parametrize(("field", "value", "message"), BAD)
def test_replay_rejects_a_bad_row_wherever_it_sits(
    field: str, value: object, message: str, position: str
):
    bad = _bad(field, value)
    with pytest.raises(InvalidMatchError, match=f"^{message}$"):
        replay(_ledger_with(bad, position), as_of=AS_OF)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize(("field", "value", "message"), BAD)
def test_prequential_rejects_a_bad_row(field: str, value: object, message: str, mode: Any):
    rows = [*(_valid(i) for i in range(6)), _bad(field, value)]
    with pytest.raises(InvalidMatchError, match=f"^{message}$"):
        prequential_predictions(rows, burn_in_matches=0, mode=mode)


AT = datetime(2026, 1, 5, 18, 0, tzinfo=UTC)
GOOD: list[Any] = [
    pytest.param({}, id="defaults"),
    pytest.param({"match_type": MatchType.TOURNAMENT}, id="match_type-member"),
    pytest.param(
        {"match_type": MatchType.SELF_REPORTED, "confirmed_by": frozenset({"syn-b", "syn-d"})},
        id="confirmed_by-frozenset",
    ),
    pytest.param({"confirmed_by": {"syn-b", "syn-d"}}, id="confirmed_by-set"),
    pytest.param({"sequence": 2}, id="sequence-int"),
    pytest.param({"sequence": _Int(2)}, id="sequence-int-subclass"),
    pytest.param({"best_of": 3, "games": ((11, 5), (11, 5))}, id="best_of-int"),
    pytest.param({"best_of": _Int(3), "games": ((11, 5), (11, 5))}, id="best_of-int-subclass"),
    pytest.param({"played_at": AT}, id="played_at-aware"),
    pytest.param({"game_format": GameFormat()}, id="game_format-member"),
]


@pytest.mark.parametrize("changes", GOOD)
def test_valid_rows_still_validate_and_record(changes: dict[str, Any]):
    row = dataclasses.replace(BASE, **changes)
    row.validate()
    assert RatingEngine().record(row).counted


@pytest.mark.parametrize("kind", ["tournament", "self_reported"])
def test_record_rejects_a_str_match_type_on_a_non_completed_row(kind: str):
    engine = RatingEngine()
    other = Match("syn-m0", ("syn-f",), ("syn-g",), ((11, 3),), ON, RatingFormat.SINGLES)
    assert engine.record(other).counted
    before = engine.to_snapshot()
    forfeit = dataclasses.replace(BASE, status=MatchStatus.FORFEIT, games=(), match_type=kind)
    with pytest.raises(InvalidMatchError, match="^match_type must be a MatchType, not str$"):
        engine.record(forfeit)
    assert engine.to_snapshot() == before


def test_both_teams_as_lists_are_rejected():
    engine = RatingEngine()
    before = engine.to_snapshot()
    row = dataclasses.replace(BASE, team_a=["syn-b", "syn-c"], team_b=["syn-d", "syn-e"])
    with pytest.raises(InvalidMatchError, match="^team_a must be a tuple, not list$"):
        row.validate()
    with pytest.raises(InvalidMatchError, match="^team_a must be a tuple, not list$"):
        engine.record(row)
    assert engine.to_snapshot() == before
