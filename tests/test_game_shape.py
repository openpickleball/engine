"""A malformed game raises InvalidMatchError, not a bare ValueError or TypeError."""

from __future__ import annotations

from datetime import date

import pytest

from open_engine import InvalidMatchError, Match, RatingEngine, RatingFormat

BAD_GAMES = [((11,),), ((11, 0, 1),), (11,)]


@pytest.mark.parametrize("games", BAD_GAMES, ids=repr)
def test_malformed_game_raises_invalid_match_error(games: object) -> None:
    m = Match("m1", ("a",), ("b",), games, date(2026, 10, 29), RatingFormat.SINGLES)  # type: ignore[arg-type]
    with pytest.raises(InvalidMatchError, match="Each game must be a pair of scores"):
        m.validate()
    with pytest.raises(InvalidMatchError, match="Each game must be a pair of scores"):
        RatingEngine().record(m)
