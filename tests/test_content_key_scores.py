"""The content key handles any valid int score, bool included (issue #21 review).

All player and match ids are synthetic.
"""

from __future__ import annotations

from datetime import date

from open_engine import Match, RatingEngine, RatingFormat, replay

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


def test_a_bool_score_has_the_same_content_as_its_int() -> None:
    eng = RatingEngine()
    eng.record(_m("syn-m1", ((11, 1),)))
    second = eng.record(_m("syn-m2", ((11, True),)))
    assert second.counted is True
    assert second.possible_duplicate_of == "syn-m1"
