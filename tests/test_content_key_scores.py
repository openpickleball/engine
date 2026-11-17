"""The content key handles any valid int score (issue #21 review).

A bool is not a valid score (issue #24), so it never reaches the key.

All player and match ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from open_engine import InvalidMatchError, Match, RatingEngine, RatingFormat, replay

D = RatingFormat.DOUBLES
ON = date(2026, 3, 1)
HUGE = 10**5000


def _m(match_id: str, games: tuple[tuple[int, int], ...]) -> Match:
    return Match(match_id, ("syn-a", "syn-b"), ("syn-c", "syn-d"), games, ON, D)


def test_a_huge_score_counts_live_and_its_copy_is_flagged() -> None:
    eng = RatingEngine()
    first = eng.record(_m("syn-m1", ((HUGE + 2, HUGE),)))
    second = eng.record(_m("syn-m2", ((HUGE + 2, HUGE),)))
    assert first.counted is True
    assert second.counted is True
    assert second.possible_duplicate_of == "syn-m1"


def test_a_huge_score_replays() -> None:
    eng = replay([_m("syn-m1", ((HUGE + 2, HUGE),))], as_of=ON)
    assert eng.replay_info is not None
    assert eng.replay_info.matches_counted == 1


def test_a_bool_score_is_rejected_before_the_content_key() -> None:
    eng = RatingEngine()
    eng.record(_m("syn-m1", ((11, 1),)))
    before = json.dumps(eng.to_snapshot(), sort_keys=True)
    with pytest.raises(InvalidMatchError, match="Game scores must be integers"):
        eng.record(_m("syn-m2", ((11, True),)))
    assert json.dumps(eng.to_snapshot(), sort_keys=True) == before
