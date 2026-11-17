"""Review fixes for match status (issue #22).

games must be a tuple or list for every status, and prequential seed
dates and anchors come from completed rows only. All ids are synthetic.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from open_engine import (
    ExternalRatingSnapshot,
    InvalidMatchError,
    InvalidSeedError,
    Match,
    MatchStatus,
    Player,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
    replay,
)

S = RatingFormat.SINGLES
D0 = date(2026, 2, 2)
PLAYERS = ["syn-q1", "syn-q2", "syn-q3", "syn-q4"]


def _m(
    match_id: str,
    games: object = ((11, 5),),
    status: MatchStatus = MatchStatus.COMPLETED,
    *,
    on: date = D0,
    team_a: tuple[str, ...] = ("syn-q1",),
    team_b: tuple[str, ...] = ("syn-q2",),
) -> Match:
    return Match(match_id, team_a, team_b, games, on, S, status=status)  # type: ignore[arg-type]


# -- F1: games must be a tuple or list ------------------------------------------

BAD_GAMES = [None, 0, "", {}]


@pytest.mark.parametrize("status", list(MatchStatus), ids=str)
@pytest.mark.parametrize("games", BAD_GAMES, ids=repr)
def test_games_that_are_not_a_tuple_or_list_raise(status: MatchStatus, games: object) -> None:
    with pytest.raises(InvalidMatchError, match=r"games must be a tuple or list"):
        _m("syn-g1", games, status).validate()


@pytest.mark.parametrize("status", list(MatchStatus), ids=str)
@pytest.mark.parametrize("games", BAD_GAMES, ids=repr)
def test_replay_rejects_games_that_are_not_a_tuple_or_list(
    status: MatchStatus, games: object
) -> None:
    with pytest.raises(InvalidMatchError, match=r"games must be a tuple or list"):
        replay([_m("syn-g1", games, status)], as_of=D0)


@pytest.mark.parametrize("games", [(), []], ids=repr)
def test_completed_empty_games_keep_their_message(games: object) -> None:
    with pytest.raises(InvalidMatchError, match=r"At least one game score is required"):
        _m("syn-g1", games).validate()


def test_forfeit_with_an_empty_list_is_valid() -> None:
    _m("syn-g1", [], MatchStatus.FORFEIT).validate()


# -- F5: seed date limit and anchor from completed rows only ----------------------


def _seed(on: date) -> Player:
    eng = RatingEngine()
    eng.seed_external_rating("syn-q1", S, ExternalRatingSnapshot("external_standard", 4.0, on))
    return eng.players["syn-q1"]


def _short_ledger() -> list[Match]:
    return [_m("syn-c1", on=D0), _m("syn-c2", ((5, 11),), on=D0 + timedelta(days=2))]


@pytest.mark.parametrize("mode", ["live", "scheduled"])
@pytest.mark.parametrize("with_forfeit", [False, True], ids=["without", "with forfeit"])
def test_seed_after_the_last_completed_match_raises(mode: str, with_forfeit: bool) -> None:
    rows = _short_ledger()
    if with_forfeit:
        rows.append(_m("syn-f1", (), MatchStatus.FORFEIT, on=D0 + timedelta(days=10)))
    with pytest.raises(InvalidSeedError, match=r"last completed ledger match"):
        prequential_predictions(
            rows,
            [_seed(D0 + timedelta(days=5))],
            mode=mode,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_no_completed_rows_means_no_seed_date_check(mode: str) -> None:
    rows = [_m("syn-f1", (), MatchStatus.FORFEIT, on=D0)]
    seed = _seed(D0 + timedelta(days=5))
    assert prequential_predictions(rows, [seed], mode=mode) == []  # type: ignore[arg-type]


def _completed_ledger() -> list[Match]:
    rng = random.Random(11)
    rows: list[Match] = []
    for i in range(40):
        a, b = rng.sample(PLAYERS, 2)
        loser = rng.randint(0, 9)
        games = ((11, loser),) if rng.random() < 0.5 else ((loser, 11),)
        on = D0 + timedelta(days=2 * i)
        rows.append(_m(f"syn-c{i:02d}", games, on=on, team_a=(a,), team_b=(b,)))
    return rows


def test_a_leading_forfeit_does_not_move_the_scheduled_anchor() -> None:
    completed = _completed_ledger()
    lead = _m("syn-f0", (), MatchStatus.FORFEIT, on=D0 - timedelta(days=4))
    without = prequential_predictions(completed, burn_in_matches=2, mode="scheduled")
    with_lead = prequential_predictions([lead, *completed], burn_in_matches=2, mode="scheduled")
    assert without
    assert with_lead == without
