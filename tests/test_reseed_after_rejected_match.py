"""A seed for a player in a seed-sensitive rejected match is refused (issue #99).

record can reject a match, or the overall doubles update of a counted
category match, for a reason a seed's rating or date decides:
BEFORE_IMPORTED_BASELINE, SINGLES_RATING_GAP, or DOUBLES_PARTNER_GAP. A
replay adds every seed before any row, so a new seed for a player in such a
match could make the replay count a row live never counted. The engine now
records those rows and refuses that seed, changing nothing. All ids are
synthetic.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    InvalidSeedError,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    RatingState,
    RejectionReason,
)

S = RatingFormat.SINGLES
D = RatingFormat.DOUBLES
MX = RatingFormat.MIXED_DOUBLES
PLAYED = date(2026, 1, 10)
EARLY = date(2026, 1, 1)
LATE = date(2026, 1, 20)
REFUSED = r"rejected .* match that named this player"

SINGLES_GAP = Config(max_singles_rating_gap=1.0)
PARTNER_GAP = Config(max_doubles_partner_gap=1.0)


def _snap(rating: float, captured_on: date = EARLY) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("syn-ext", rating, captured_on)


def _match(
    match_id: str, team_a: tuple[str, ...], team_b: tuple[str, ...], fmt: RatingFormat
) -> Match:
    return Match(match_id, team_a, team_b, ((11, 6),), PLAYED, fmt)


def _seed_row(player_id: str, fmt: RatingFormat, rating: float) -> Player:
    state = RatingState(
        rating=rating,
        rd=Config().external_seed_rd,
        rd_as_of=EARLY,
        initial_rating=rating,
        initial_source="syn-ext",
        initial_source_date=EARLY,
    )
    return Player(id=player_id, ratings={fmt: state})


def _assert_refused_unchanged(engine: RatingEngine, call: Callable[[], object]) -> None:
    before = engine.to_snapshot()
    with pytest.raises(InvalidSeedError, match=REFUSED):
        call()
    assert engine.to_snapshot() == before


# -- BEFORE_IMPORTED_BASELINE ----------------------------------------------------


def _singles_baseline_engine() -> RatingEngine:
    engine = RatingEngine()
    engine.seed_external_rating("syn-a", S, _snap(4.0, LATE))
    engine.seed_external_rating("syn-b", S, _snap(4.0, LATE))
    result = engine.record(_match("syn-m1", ("syn-a",), ("syn-b",), S))
    assert result.reason is RejectionReason.BEFORE_IMPORTED_BASELINE
    return engine


@pytest.mark.parametrize("replace", [True, False])
def test_issue_repro_singles_baseline_reseed_is_refused(replace: bool) -> None:
    engine = _singles_baseline_engine()
    _assert_refused_unchanged(
        engine,
        lambda: engine.seed_external_rating(
            "syn-a", S, _snap(4.0, EARLY), replace_unplayed_seed=replace
        ),
    )


def test_doubles_baseline_reseed_is_refused() -> None:
    engine = RatingEngine()
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        engine.seed_external_rating(pid, D, _snap(4.0, LATE))
    result = engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), D))
    assert result.reason is RejectionReason.BEFORE_IMPORTED_BASELINE
    _assert_refused_unchanged(
        engine,
        lambda: engine.seed_external_rating(
            "syn-c", D, _snap(4.0, EARLY), replace_unplayed_seed=True
        ),
    )


def _derived_frozen_engine() -> RatingEngine:
    """A mixed match whose players' states all derive from dated doubles seeds."""
    engine = RatingEngine()
    for pid in ("syn-a", "syn-b", "syn-c", "syn-d"):
        engine.seed_external_rating(pid, D, _snap(4.0, LATE))
    result = engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), MX))
    assert result.reason is RejectionReason.BEFORE_IMPORTED_BASELINE
    return engine


def test_all_derived_frozen_category_doubles_reseed_is_refused() -> None:
    engine = _derived_frozen_engine()
    _assert_refused_unchanged(
        engine,
        lambda: engine.seed_external_rating(
            "syn-b", D, _snap(4.0, EARLY), replace_unplayed_seed=True
        ),
    )


def test_all_derived_frozen_category_direct_seed_is_refused() -> None:
    engine = _derived_frozen_engine()
    _assert_refused_unchanged(
        engine, lambda: engine.seed_external_rating("syn-b", MX, _snap(4.0, EARLY))
    )


# -- SINGLES_RATING_GAP ------------------------------------------------------------


def _singles_gap_engine() -> RatingEngine:
    engine = RatingEngine(SINGLES_GAP)
    engine.seed_external_rating("syn-a", S, _snap(5.0))
    result = engine.record(_match("syn-m1", ("syn-a",), ("syn-u",), S))
    assert result.reason is RejectionReason.SINGLES_RATING_GAP
    return engine


def test_singles_gap_reseed_is_refused() -> None:
    engine = _singles_gap_engine()
    _assert_refused_unchanged(
        engine,
        lambda: engine.seed_external_rating("syn-a", S, _snap(3.8), replace_unplayed_seed=True),
    )


def test_singles_gap_first_seed_for_unknown_player_is_refused() -> None:
    engine = _singles_gap_engine()
    assert "syn-u" not in engine.players
    _assert_refused_unchanged(engine, lambda: engine.seed_external_rating("syn-u", S, _snap(4.5)))
    assert "syn-u" not in engine.players


def test_singles_gap_add_player_is_refused() -> None:
    engine = _singles_gap_engine()
    _assert_refused_unchanged(engine, lambda: engine.add_player(_seed_row("syn-u", S, 4.5)))
    assert "syn-u" not in engine.players


# -- DOUBLES_PARTNER_GAP -----------------------------------------------------------


def test_partner_gap_reseed_is_refused() -> None:
    engine = RatingEngine(PARTNER_GAP)
    engine.seed_external_rating("syn-a", D, _snap(5.0))
    engine.seed_external_rating("syn-b", D, _snap(3.0))
    engine.seed_external_rating("syn-c", D, _snap(4.0))
    engine.seed_external_rating("syn-d", D, _snap(4.0))
    result = engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), D))
    assert result.reason is RejectionReason.DOUBLES_PARTNER_GAP
    _assert_refused_unchanged(
        engine,
        lambda: engine.seed_external_rating("syn-b", D, _snap(4.5), replace_unplayed_seed=True),
    )


def test_partner_gap_first_seed_for_unknown_partner_is_refused() -> None:
    engine = RatingEngine(PARTNER_GAP)
    engine.seed_external_rating("syn-a", D, _snap(5.0))
    result = engine.record(_match("syn-m1", ("syn-a", "syn-u"), ("syn-c", "syn-d"), D))
    assert result.reason is RejectionReason.DOUBLES_PARTNER_GAP
    _assert_refused_unchanged(engine, lambda: engine.seed_external_rating("syn-u", D, _snap(4.5)))
    assert "syn-u" not in engine.players
