"""Singles rules the rest of the suite does not cover (issue #26). Player ids are invented."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidMatchError,
    Match,
    RatingEngine,
    RatingFormat,
    RejectionReason,
)

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
CAPTURED = date(2026, 1, 5)
ON = date(2026, 2, 2)


def seeded(cfg: Config, fmt: RatingFormat, ratings: dict[str, float]) -> RatingEngine:
    engine = RatingEngine(cfg)
    for pid, rating in ratings.items():
        snap = ExternalRatingSnapshot("external_standard", rating, CAPTURED)
        engine.seed_external_rating(pid, fmt, snap)
    return engine


def singles(match_id: str, a: str, b: str, on: date = ON) -> Match:
    return Match(match_id, (a,), (b,), ((11, 6),), on, S)


@pytest.mark.parametrize(
    ("rating_a", "rating_b", "counted"),
    [(3.0, 3.5, True), (3.5, 3.0, True), (3.0, 3.51, False), (3.51, 3.0, False)],
)
def test_singles_gap_counts_at_the_limit_and_rejects_past_it(
    rating_a: float, rating_b: float, counted: bool
) -> None:
    engine = seeded(Config(max_singles_rating_gap=0.5), S, {"syn-a": rating_a, "syn-b": rating_b})
    result = engine.record(singles("m1", "syn-a", "syn-b"))
    assert result.counted is counted
    assert result.reason is (None if counted else RejectionReason.SINGLES_RATING_GAP)


def test_partner_gap_does_not_apply_to_singles() -> None:
    engine = seeded(Config(max_doubles_partner_gap=0.1), S, {"syn-a": 3.0, "syn-b": 6.0})
    assert engine.record(singles("m1", "syn-a", "syn-b")).counted


def test_singles_gap_does_not_apply_to_doubles() -> None:
    ratings = {"syn-a": 3.0, "syn-b": 3.0, "syn-c": 6.0, "syn-d": 6.0}
    engine = seeded(Config(max_singles_rating_gap=0.5), D, ratings)
    match = Match("m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 6),), ON, D)
    assert engine.record(match).counted


def test_singles_records_opponents_and_no_partners() -> None:
    engine = RatingEngine()
    for i, opponent in enumerate(("syn-b", "syn-c", "syn-d")):
        assert engine.record(singles(f"m{i}", "syn-a", opponent, ON + timedelta(days=i))).counted
    state = engine.state("syn-a", S)
    assert state.partners == {}
    assert state.opponents == {
        "syn-b": ON,
        "syn-c": ON + timedelta(days=1),
        "syn-d": ON + timedelta(days=2),
    }


def test_singles_and_doubles_are_separate_pools() -> None:
    engine = RatingEngine()
    assert engine.record(singles("m1", "syn-a", "syn-b")).counted
    assert D not in engine.player("syn-a").ratings
    singles_before = engine.state("syn-a", S).rating
    match = Match("m2", ("syn-a", "syn-c"), ("syn-b", "syn-d"), ((11, 3),), ON, D)
    assert engine.record(match).counted
    assert engine.state("syn-a", S).rating == singles_before
    assert engine.state("syn-a", S).matches_played == 1
    assert engine.state("syn-a", D).matches_played == 1


def test_singles_needs_one_player_per_team() -> None:
    match = Match("m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), ((11, 6),), ON, S)
    with pytest.raises(InvalidMatchError, match="singles requires 1 player"):
        RatingEngine().record(match)
