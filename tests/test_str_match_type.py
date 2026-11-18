"""A match_type given as its str value gets the same rules as the enum member.

Match.validate accepts the str value, so a self-reported match written as
"self_reported" must still face the confirmation rule, the repeat discount
and limit, and the self-reported log. All ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from open_engine import (
    Match,
    MatchType,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    replay,
)

S = RatingFormat.SINGLES
START = date(2026, 1, 5)


def _row(match_id: str, on: date, kind: str, confirmed: frozenset[str]) -> Match:
    return Match(
        match_id,
        ("syn-a",),
        ("syn-b",),
        ((11, 5),),
        on,
        S,
        match_type=kind,  # type: ignore[arg-type]
        confirmed_by=confirmed,
    )


BOTH = frozenset({"syn-a", "syn-b"})


def test_unconfirmed_str_self_reported_is_rejected() -> None:
    engine = RatingEngine()
    result = engine.record(_row("syn-m1", START, "self_reported", frozenset({"syn-a"})))
    assert result.counted is False
    assert result.reason is RejectionReason.SELF_REPORTED_UNCONFIRMED
    assert "syn-a" not in engine.players


def test_confirmed_str_self_reported_is_logged_and_not_verified() -> None:
    engine = RatingEngine()
    assert engine.record(_row("syn-m1", START, "self_reported", BOTH)).counted
    assert [dates for dates in engine.self_reported_log.values()] == [[START]]
    state = engine.players["syn-a"].ratings[S]
    assert (state.matches_played, state.verified_matches_played) == (1, 0)
    RatingEngine.from_snapshot(json.loads(json.dumps(engine.to_snapshot())))


def test_str_self_reported_gets_the_repeat_discount_and_limit() -> None:
    as_str = RatingEngine()
    as_enum = RatingEngine()
    results = []
    for i in range(6):
        on = START + timedelta(days=i)
        got = as_str.record(_row(f"syn-m{i}", on, "self_reported", BOTH))
        want = as_enum.record(_row(f"syn-m{i}", on, MatchType.SELF_REPORTED, BOTH))
        assert got == want
        results.append(got)
    assert results[-1].reason is RejectionReason.SELF_REPORTED_REPEAT_LIMIT
    assert as_str.players == as_enum.players


def test_str_self_reported_replay_snapshots_load_and_resume() -> None:
    rows = [
        _row(f"syn-m{i}", START + timedelta(days=10 * i), "self_reported", BOTH) for i in range(4)
    ]
    full = replay(rows, as_of=START + timedelta(days=60))
    RatingEngine.from_snapshot(json.loads(json.dumps(full.to_snapshot())))
    checkpoint = replay(rows, as_of=START + timedelta(days=15))
    snap = json.loads(json.dumps(checkpoint.to_snapshot()))
    resumed = replay(rows, as_of=START + timedelta(days=60), from_snapshot=snap)
    assert resumed.players == full.players
    assert resumed.replay_info == full.replay_info


def test_str_tournament_counts_as_verified() -> None:
    engine = RatingEngine()
    assert engine.record(_row("syn-m1", START, "tournament", frozenset())).counted
    state = engine.players["syn-a"].ratings[S]
    assert (state.matches_played, state.verified_matches_played) == (1, 1)
