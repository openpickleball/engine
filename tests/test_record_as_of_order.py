"""record checks as_of against the match date before any rejection (issue #72).

All player and match ids are synthetic.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    InvalidMatchError,
    Match,
    MatchStatus,
    RatingEngine,
    RatingFormat,
    ScoringType,
    replay,
)

S = RatingFormat.SINGLES
PLAYED = date(2026, 10, 29)
EARLY = PLAYED - timedelta(days=28)
RALLY_ONLY = Config(allowed_game_formats=frozenset({(ScoringType.RALLY, 15)}))


def _m(
    match_id: str,
    games: tuple[tuple[int, int], ...] = ((11, 5),),
    status: MatchStatus = MatchStatus.COMPLETED,
    *,
    best_of: int = 1,
) -> Match:
    return Match(
        match_id,
        ("syn-a",),
        ("syn-b",),
        games,
        PLAYED,
        S,
        best_of=best_of,
        status=status,
    )


def _processed_engine() -> RatingEngine:
    return replay([_m("syn-m1")], as_of=PLAYED)


CASES = {
    "forfeit": (RatingEngine, _m("syn-m1", (), MatchStatus.FORFEIT)),
    "retired": (RatingEngine, _m("syn-m1", ((11, 5),), MatchStatus.RETIRED, best_of=3)),
    "incomplete": (
        RatingEngine,
        _m("syn-m1", ((11, 5), (5, 11)), MatchStatus.INCOMPLETE, best_of=3),
    ),
    "game format": (lambda: RatingEngine(RALLY_ONLY), _m("syn-m1")),
    "processed id": (_processed_engine, _m("syn-m1")),
    "processed id forfeit": (_processed_engine, _m("syn-m1", (), MatchStatus.FORFEIT)),
}


@pytest.mark.parametrize(("make", "match"), list(CASES.values()), ids=list(CASES))
def test_record_rejects_early_as_of_for_every_rejection(make: object, match: Match) -> None:
    assert callable(make)
    eng = make()
    assert isinstance(eng, RatingEngine)
    before = eng.to_snapshot()
    with pytest.raises(InvalidMatchError, match="as_of cannot be earlier than the match date"):
        eng.record(match, as_of=EARLY)
    assert eng.to_snapshot() == before


def test_record_with_as_of_on_the_match_date_still_rejects() -> None:
    eng = RatingEngine()
    result = eng.record(_m("syn-m1", (), MatchStatus.FORFEIT), as_of=PLAYED)
    assert result.counted is False


def test_replay_skips_a_non_completed_row_after_as_of() -> None:
    later = Match(
        "syn-m2",
        ("syn-a",),
        ("syn-b",),
        (),
        PLAYED + timedelta(days=5),
        S,
        status=MatchStatus.FORFEIT,
    )
    eng = replay([_m("syn-m1"), later], as_of=PLAYED)
    assert eng.replay_info is not None
    assert eng.replay_info.matches_considered == 1
    assert eng.processed_match_ids == {"syn-m1"}
