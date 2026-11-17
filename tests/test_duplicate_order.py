"""Processing order alone picks which report of one match is the copy (issue #21).

This pins the documented limit: under reject_content_duplicates=True a weaker
report that comes first in ledger order survives. All ids are synthetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from open_engine import (
    Config,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    replay,
)

ON = date(2026, 3, 1)
TEAM_A = ("syn-a", "syn-b")
TEAM_B = ("syn-c", "syn-d")

TOURNAMENT = Match(
    "syn-a1",
    TEAM_A,
    TEAM_B,
    ((11, 7),),
    ON,
    RatingFormat.DOUBLES,
    match_type=MatchType.TOURNAMENT,
    played_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
)
SELF_REPORT = Match(
    "syn-z9",
    TEAM_B,
    TEAM_A,
    ((7, 11),),
    ON,
    RatingFormat.DOUBLES,
    match_type=MatchType.SELF_REPORTED,
    confirmed_by=frozenset(TEAM_A + TEAM_B),
)


def test_reject_keeps_an_untimed_self_report_over_a_timed_tournament_row() -> None:
    eng = replay([TOURNAMENT, SELF_REPORT], cfg=Config(reject_content_duplicates=True), as_of=ON)
    assert eng.processed_match_ids == {"syn-z9"}
    assert eng.possible_duplicates == {"syn-a1": "syn-z9"}


def test_default_flags_the_timed_tournament_row() -> None:
    eng = replay([TOURNAMENT, SELF_REPORT], as_of=ON)
    assert eng.processed_match_ids == {"syn-a1", "syn-z9"}
    assert eng.possible_duplicates == {"syn-a1": "syn-z9"}


def test_live_order_keeps_the_first_arrival() -> None:
    eng = RatingEngine(Config(reject_content_duplicates=True))
    assert eng.record(TOURNAMENT).counted is True
    later = eng.record(SELF_REPORT)
    assert later.reason is RejectionReason.POSSIBLE_DUPLICATE
    assert later.possible_duplicate_of == "syn-a1"
