"""Match.validate checks ids, played_on, and score types before any write (issue #24).

A failed record() leaves the engine exactly as it was, and a bad row in a
ledger raises InvalidMatchError wherever it sits. All ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from open_engine import (
    InvalidMatchError,
    Match,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)

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
    with pytest.raises(InvalidMatchError, match="played_on must be a plain date"):
        _m("syn-m1", played_on=played_on).validate()


@pytest.mark.parametrize("played_on", NOT_DATES, ids=["datetime", "str", "none", "int"])
def test_non_date_played_on_changes_nothing_live(played_on: object) -> None:
    eng = _engine()
    before = _state(eng)
    with pytest.raises(InvalidMatchError, match="played_on must be a plain date"):
        eng.record(_m("syn-m1", played_on=played_on))
    assert _state(eng) == before


@pytest.mark.parametrize("first", [False, True], ids=["bad_last", "bad_first"])
@pytest.mark.parametrize("played_on", NOT_DATES, ids=["datetime", "str", "none", "int"])
def test_non_date_played_on_is_rejected_in_a_ledger(played_on: object, first: bool) -> None:
    good = _m("syn-m1")
    bad = _m("syn-m2", played_on=played_on)
    with pytest.raises(InvalidMatchError, match="played_on must be a plain date"):
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


@pytest.mark.parametrize(
    "bad",
    [_m("syn-m2", played_on=datetime(2026, 3, 2, 10)), _m("syn-m2", games=((11, True),))],
    ids=["datetime_played_on", "bool_score"],
)
def test_prequential_rejects_the_new_cases(bad: Match) -> None:
    with pytest.raises(InvalidMatchError):
        prequential_predictions([_m("syn-m1"), bad])


NOT_AS_OF = [datetime(2026, 3, 31, 10), "2026-03-31", 0]


@pytest.mark.parametrize("as_of", NOT_AS_OF, ids=["datetime", "str", "falsy_int"])
@pytest.mark.parametrize("repeat", [False, True], ids=["new_id", "processed_id"])
def test_record_rejects_an_as_of_that_is_not_a_plain_date(as_of: object, repeat: bool) -> None:
    eng = _engine()
    before = _state(eng)
    match_id = "syn-m0" if repeat else "syn-m1"
    with pytest.raises(InvalidMatchError, match="as_of must be a plain date"):
        eng.record(_m(match_id), as_of=as_of)  # type: ignore[arg-type]
    assert _state(eng) == before
