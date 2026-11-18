"""Established needs counted verified matches (issue #59).

All player and match ids are synthetic.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ConfigError,
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    MatchType,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
)

D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
START = date(2026, 1, 5)
VERIFIED = (MatchType.TOURNAMENT, MatchType.LEAGUE, MatchType.REC_VERIFIED)


def self_reported_wins(count: int) -> list[Match]:
    """Weekly self-reported doubles wins 11-9 for p, each with a new partner and opponents."""
    return [
        Match(
            match_id=f"sr{i}",
            team_a=("p", f"q{i}"),
            team_b=(f"r{i}", f"s{i}"),
            games=((11, 9),),
            played_on=START + timedelta(weeks=i),
            rating_format=D,
            match_type=MatchType.SELF_REPORTED,
            confirmed_by=frozenset({"p", f"r{i}"}),
        )
        for i in range(count)
    ]


def verified_wins(count: int, after: date) -> list[Match]:
    """Weekly recreational verified doubles wins 11-9 for p, after the given date."""
    return [
        Match(
            match_id=f"v{i}",
            team_a=("p", f"vq{i}"),
            team_b=(f"vr{i}", f"vs{i}"),
            games=((11, 9),),
            played_on=after + timedelta(weeks=i + 1),
            rating_format=D,
            match_type=MatchType.REC_VERIFIED,
        )
        for i in range(count)
    ]


def recorded(matches: list[Match], cfg: Config | None = None) -> RatingEngine:
    engine = RatingEngine(cfg)
    for match in matches:
        assert engine.record(match).counted
    return engine


# -- Config field --------------------------------------------------------------


def test_default_minimum_is_3() -> None:
    assert Config().established_min_verified_matches == 3


@pytest.mark.parametrize("value", [-1, True, False, 3.0, "3", None])
def test_minimum_must_be_a_nonnegative_int(value: object) -> None:
    with pytest.raises(
        ConfigError, match="established_min_verified_matches must be a nonnegative int"
    ):
        Config(established_min_verified_matches=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [0, 1, 10**9])
def test_minimum_accepts_nonnegative_ints(value: int) -> None:
    assert Config(established_min_verified_matches=value).established_min_verified_matches == value


@pytest.mark.parametrize("value", [0, 4])
def test_minimum_is_in_fingerprint(value: int) -> None:
    assert Config(established_min_verified_matches=value).fingerprint() != Config().fingerprint()


def test_minimum_round_trips_through_to_dict_and_json() -> None:
    cfg = Config(established_min_verified_matches=7)
    data = cfg.to_dict()
    assert data["established_min_verified_matches"] == 7
    for loaded in (Config.from_dict(data), Config.from_dict(json.loads(json.dumps(data)))):
        assert loaded == cfg
        assert loaded.established_min_verified_matches == 7
        assert loaded.fingerprint() == cfg.fingerprint()


# -- established ---------------------------------------------------------------


@pytest.mark.parametrize("count", [25, 28, 30])
def test_self_reported_only_never_established(count: int) -> None:
    matches = self_reported_wins(count)
    summary = recorded(matches).player_summary("p", D, as_of=matches[-1].played_on)
    assert summary.reliability >= 60.0
    assert summary.established is False


def test_established_after_verified_matches() -> None:
    sr = self_reported_wins(30)
    extra = verified_wins(3, sr[-1].played_on)
    two = recorded(sr + extra[:2]).player_summary("p", D, as_of=extra[1].played_on)
    assert two.reliability >= 60.0
    assert two.established is False
    three = recorded(sr + extra).player_summary("p", D, as_of=extra[2].played_on)
    assert three.reliability >= 60.0
    assert three.established is True


@pytest.mark.parametrize("count", [1, 10, 20, 22, 24, 25, 26, 30])
def test_zero_minimum_keeps_reliability_rule(count: int) -> None:
    matches = self_reported_wins(count)
    cfg = Config(established_min_verified_matches=0)
    summary = recorded(matches, cfg).player_summary("p", D, as_of=matches[-1].played_on)
    assert summary.established is (summary.reliability >= cfg.established_reliability_threshold)


def test_minimum_does_not_bypass_reliability() -> None:
    matches = verified_wins(3, START)
    summary = recorded(matches).player_summary("p", D, as_of=matches[-1].played_on)
    assert summary.reliability < 60.0
    assert summary.established is False


# -- counting ------------------------------------------------------------------


def _one(match_type: MatchType, rating_format: RatingFormat = D) -> Match:
    return Match(
        "c1",
        ("a", "b"),
        ("c", "d"),
        ((11, 7),),
        START,
        rating_format,
        match_type=match_type,
        confirmed_by=frozenset({"a", "c"}),
    )


@pytest.mark.parametrize("match_type", VERIFIED)
def test_verified_types_count(match_type: MatchType) -> None:
    engine = recorded([_one(match_type)])
    for pid in "abcd":
        state = engine.players[pid].ratings[D]
        assert (state.matches_played, state.verified_matches_played) == (1, 1)


def test_self_reported_does_not_count() -> None:
    engine = recorded([_one(MatchType.SELF_REPORTED)])
    for pid in "abcd":
        state = engine.players[pid].ratings[D]
        assert (state.matches_played, state.verified_matches_played) == (1, 0)


def test_frozen_player_gains_no_verified_match() -> None:
    engine = RatingEngine()
    engine.seed_external_rating("a", D, ExternalRatingSnapshot("ext", 4.0, START))
    result = engine.record(_one(MatchType.TOURNAMENT))
    assert result.frozen == ("a",)
    frozen = engine.players["a"].ratings[D]
    assert (frozen.matches_played, frozen.verified_matches_played) == (0, 0)
    moved = engine.players["b"].ratings[D]
    assert (moved.matches_played, moved.verified_matches_played) == (1, 1)


@pytest.mark.parametrize(
    ("match_type", "verified"), [(MatchType.LEAGUE, 1), (MatchType.SELF_REPORTED, 0)]
)
def test_category_match_counts_in_category_and_doubles(
    match_type: MatchType, verified: int
) -> None:
    engine = recorded([_one(match_type, MX)])
    for pid in "abcd":
        for fmt in (MX, D):
            state = engine.players[pid].ratings[fmt]
            assert (state.matches_played, state.verified_matches_played) == (1, verified)


def test_category_without_parent_feed_counts_only_in_category() -> None:
    engine = recorded([_one(MatchType.LEAGUE, MX)], Config(category_feeds_parent=False))
    for pid in "abcd":
        assert engine.players[pid].ratings[MX].verified_matches_played == 1
        assert D not in engine.players[pid].ratings


def test_derived_category_state_starts_at_zero() -> None:
    engine = recorded([_one(MatchType.TOURNAMENT)])
    assert engine.players["a"].ratings[D].verified_matches_played == 1
    derived = engine.state("a", MX)
    assert derived.initial_source == "open_parent:doubles"
    assert (derived.matches_played, derived.verified_matches_played) == (0, 0)


def test_new_state_starts_at_zero() -> None:
    assert RatingState(rating=3.5, rd=0.8).verified_matches_played == 0


# -- seeds ---------------------------------------------------------------------


def _seed(**changes: object) -> Player:
    state = RatingState(rating=3.5, rd=Config().new_player_rd, **changes)  # type: ignore[arg-type]
    return Player("seeded", ratings={D: state})


def test_seed_with_verified_matches_is_rejected() -> None:
    with pytest.raises(InvalidSeedError, match="verified_matches_played must be 0"):
        RatingEngine().add_player(_seed(verified_matches_played=1))


@pytest.mark.parametrize("value", [True, 0.0, "0", None])
def test_seed_verified_matches_must_be_an_int(value: object) -> None:
    with pytest.raises(InvalidSeedError, match="verified_matches_played must be an int"):
        RatingEngine().add_player(_seed(verified_matches_played=value))


def test_external_seed_starts_at_zero() -> None:
    engine = RatingEngine()
    state = engine.seed_external_rating("e", D, ExternalRatingSnapshot("ext", 4.0, START, 90.0))
    assert state.verified_matches_played == 0
