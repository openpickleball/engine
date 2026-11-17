"""Match.validate checks ids, played_on, and score types before any write (issue #24).

A failed record() leaves the engine exactly as it was, and a bad row in a
ledger raises InvalidMatchError wherever it sits. All ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from open_engine import InvalidMatchError, Match, RatingEngine, RatingFormat, replay

D = RatingFormat.DOUBLES
ON = date(2026, 3, 2)
AS_OF = date(2026, 3, 31)
TEAM_A = ("syn-a", "syn-b")
TEAM_B = ("syn-c", "syn-d")


def _m(
    match_id: str,
    *,
    team_a: tuple[object, ...] = TEAM_A,
    team_b: tuple[object, ...] = TEAM_B,
    games: tuple[tuple[object, object], ...] = ((11, 5),),
    played_on: object = ON,
) -> Match:
    return Match(match_id, team_a, team_b, games, played_on, D)  # type: ignore[arg-type]


def _engine() -> RatingEngine:
    """A replayed engine with players, so a stray write would show."""
    return replay([_m("syn-m0", played_on=date(2026, 3, 1))], as_of=date(2026, 3, 1))


def _state(eng: RatingEngine) -> str:
    return json.dumps(eng.to_snapshot(), sort_keys=True)


@pytest.mark.parametrize("bad", ["", " ", "\t", 7, None])
@pytest.mark.parametrize("side", ["team_a", "team_b"])
@pytest.mark.parametrize("position", [0, 1])
def test_bad_player_id_changes_nothing(bad: object, side: str, position: int) -> None:
    team = list(TEAM_A if side == "team_a" else TEAM_B)
    team[position] = bad
    # A new id beside the bad one would be created first by a partial write.
    team[1 - position] = "syn-new"
    eng = _engine()
    before = _state(eng)
    with pytest.raises(InvalidMatchError, match="Player ids must be non-empty strings"):
        eng.record(_m("syn-m1", **{side: tuple(team)}))  # type: ignore[arg-type]
    assert _state(eng) == before
    assert "syn-new" not in eng.players


NOT_DATES = [datetime(2026, 3, 2, 10), "2026-03-02", None, 20260302]


@pytest.mark.parametrize("played_on", NOT_DATES, ids=["datetime", "str", "none", "int"])
def test_non_date_played_on_is_rejected_by_validate(played_on: object) -> None:
    with pytest.raises(InvalidMatchError, match="played_on must be a date"):
        _m("syn-m1", played_on=played_on).validate()


@pytest.mark.parametrize("played_on", NOT_DATES, ids=["datetime", "str", "none", "int"])
def test_non_date_played_on_changes_nothing_live(played_on: object) -> None:
    eng = _engine()
    before = _state(eng)
    with pytest.raises(InvalidMatchError, match="played_on must be a date"):
        eng.record(_m("syn-m1", played_on=played_on))
    assert _state(eng) == before


@pytest.mark.parametrize("first", [False, True], ids=["bad_last", "bad_first"])
@pytest.mark.parametrize("played_on", NOT_DATES, ids=["datetime", "str", "none", "int"])
def test_non_date_played_on_is_rejected_in_a_ledger(played_on: object, first: bool) -> None:
    good = _m("syn-m1")
    bad = _m("syn-m2", played_on=played_on)
    with pytest.raises(InvalidMatchError, match="played_on must be a date"):
        replay([bad, good] if first else [good, bad], as_of=AS_OF)


@pytest.mark.parametrize("games", [((11, True),), ((True, False),), ((False, 11),)])
def test_bool_score_is_rejected(games: tuple[tuple[object, object], ...]) -> None:
    eng = _engine()
    before = _state(eng)
    with pytest.raises(InvalidMatchError, match="Game scores must be integers"):
        eng.record(_m("syn-m1", games=games))
    assert _state(eng) == before


def test_bool_score_is_rejected_in_a_ledger() -> None:
    with pytest.raises(InvalidMatchError, match="Game scores must be integers"):
        replay([_m("syn-m1"), _m("syn-m2", games=((11, True),))], as_of=AS_OF)
