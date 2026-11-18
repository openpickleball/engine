"""A match_type given as its str value is rejected (issues #65 and #83).

Match.validate requires a MatchType member, so a str such as "self_reported"
or "tournament" raises InvalidMatchError from validate, record, and replay,
even when it equals a member's value. record leaves the engine unchanged.
All ids are synthetic.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from open_engine import (
    InvalidMatchError,
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    replay,
)

S = RatingFormat.SINGLES
START = date(2026, 1, 5)
BOTH = frozenset({"syn-a", "syn-b"})
MESSAGE = "^match_type must be a MatchType, not str$"
STR_KINDS = ["self_reported", "tournament"]


def _row(match_id: str, on: date, kind: object) -> Match:
    return Match(
        match_id,
        ("syn-a",),
        ("syn-b",),
        ((11, 5),),
        on,
        S,
        match_type=kind,  # type: ignore[arg-type]
        confirmed_by=BOTH,
    )


@pytest.mark.parametrize("kind", STR_KINDS)
def test_validate_rejects_a_str_match_type(kind: str) -> None:
    with pytest.raises(InvalidMatchError, match=MESSAGE):
        _row("syn-m1", START, kind).validate()


@pytest.mark.parametrize("kind", STR_KINDS)
def test_record_rejects_a_str_match_type_and_changes_nothing(kind: str) -> None:
    engine = RatingEngine()
    assert engine.record(_row("syn-m0", START, MatchType(kind))).counted
    before = engine.to_snapshot()
    with pytest.raises(InvalidMatchError, match=MESSAGE):
        engine.record(_row("syn-m1", START + timedelta(days=1), kind))
    assert engine.to_snapshot() == before


@pytest.mark.parametrize("kind", STR_KINDS)
def test_replay_rejects_a_str_match_type(kind: str) -> None:
    rows = [_row(f"syn-m{i}", START + timedelta(days=i), MatchType(kind)) for i in range(3)]
    rows.append(_row("syn-m9", START + timedelta(days=9), kind))
    with pytest.raises(InvalidMatchError, match=MESSAGE):
        replay(rows, as_of=START + timedelta(days=30))


@pytest.mark.parametrize("kind", STR_KINDS)
def test_the_member_of_the_same_value_still_counts(kind: str) -> None:
    engine = RatingEngine()
    assert engine.record(_row("syn-m1", START, MatchType(kind))).counted
