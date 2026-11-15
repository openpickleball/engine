"""Match and player ids must be non-empty strings before anything is committed."""

from __future__ import annotations

from datetime import date

import pytest

from open_engine import InvalidMatchError, Match, RatingEngine, RatingFormat

D = RatingFormat.DOUBLES
ON = date(2026, 10, 29)
BAD_IDS = ["", " ", 5, None]


def _match(team_a: tuple[object, ...], match_id: object = "m1") -> Match:
    return Match(match_id, team_a, ("c", "d"), ((11, 7),), ON, D)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
def test_record_with_a_bad_team_id_changes_nothing_on_any_retry(bad: object) -> None:
    eng = RatingEngine()
    eng.seed_provisional_rating("a", D)
    before = eng.state("a", D).rating
    for _ in range(3):
        with pytest.raises(InvalidMatchError, match="Player ids must be non-empty strings"):
            eng.record(_match(("a", bad)))
    assert eng.state("a", D).rating == before
    assert eng.state("a", D).matches_played == 0
    assert sorted(eng.players) == ["a"]
    assert eng.processed_match_ids == set()


@pytest.mark.parametrize("position", [0, 1])
@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
def test_validate_rejects_a_bad_team_id(bad: object, position: int) -> None:
    team = ["a", "b"]
    team[position] = bad  # type: ignore[call-overload]
    with pytest.raises(InvalidMatchError, match="Player ids must be non-empty strings"):
        _match(tuple(team)).validate()


@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
def test_validate_rejects_a_bad_match_id(bad: object) -> None:
    with pytest.raises(InvalidMatchError, match="match_id is required"):
        _match(("a", "b"), match_id=bad).validate()


@pytest.mark.parametrize("bad", BAD_IDS, ids=repr)
def test_predictions_reject_a_bad_team_id(bad: object) -> None:
    eng = RatingEngine()
    with pytest.raises(InvalidMatchError, match="Player ids must be non-empty strings"):
        eng.predict_point_share(("a", bad), ("c", "d"), D)  # type: ignore[arg-type]
    assert eng.players == {}
