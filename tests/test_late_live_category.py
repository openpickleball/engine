"""Late live matches against dates a rating already reflects (issue #19, from review).

A category rating copied from overall doubles keeps the doubles rd_as_of
but has no last_played. A match dated before that rd_as_of is late too,
unless rd_as_of is only an imported capture date. All player ids are
synthetic.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    RatingEngine,
    RatingFormat,
    RejectionReason,
    ledger_order,
)

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
MX = RatingFormat.MIXED_DOUBLES
NEWEST = date(2026, 10, 1)
OLDER = date(2026, 1, 1)


def _match(
    match_id: str,
    team_a: tuple[str, ...],
    team_b: tuple[str, ...],
    on: date,
    fmt: RatingFormat = D,
) -> Match:
    return Match(match_id, team_a, team_b, ((11, 7),), on, fmt)


def _snapshot(rating: float, captured_on: date) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("external_standard", rating, captured_on)


def test_category_match_before_the_copied_doubles_date_is_late() -> None:
    engine = RatingEngine()
    engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    mixed = engine.record(_match("syn-m2", ("syn-a", "syn-w"), ("syn-x", "syn-y"), OLDER, MX))
    assert mixed.counted
    assert mixed.late is True
    assert mixed.parent is not None
    assert mixed.parent.late is True


def test_category_match_is_late_when_its_parent_is_rejected() -> None:
    engine = RatingEngine(Config(max_doubles_partner_gap=0.5))
    engine.seed_external_rating("syn-w", D, _snapshot(5.0, date(2025, 6, 1)))
    engine.seed_external_rating("syn-w", MX, _snapshot(3.5, date(2025, 6, 1)))
    engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    mixed = engine.record(_match("syn-m2", ("syn-a", "syn-w"), ("syn-x", "syn-y"), OLDER, MX))
    assert mixed.counted
    assert mixed.late is True
    assert mixed.parent is not None
    assert mixed.parent.reason is RejectionReason.DOUBLES_PARTNER_GAP
    assert mixed.parent.late is False


def test_match_before_a_plain_imported_baseline_is_not_late() -> None:
    engine = RatingEngine()
    engine.seed_external_rating("syn-a", D, _snapshot(3.5, NEWEST))
    result = engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), OLDER))
    assert result.counted
    assert result.frozen == ("syn-a",)
    assert result.late is False


def test_category_match_before_an_unplayed_imported_parent_is_not_late() -> None:
    engine = RatingEngine()
    engine.seed_external_rating("syn-a", D, _snapshot(3.5, NEWEST))
    mixed = engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), OLDER, MX))
    assert mixed.counted
    assert mixed.late is False
    assert mixed.parent is not None
    assert mixed.parent.late is False


def test_late_match_adds_a_new_partner_with_the_late_date() -> None:
    engine = RatingEngine()
    engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    late = engine.record(_match("syn-m2", ("syn-a", "syn-e"), ("syn-c", "syn-d"), OLDER))
    assert late.late is True
    partners = engine.state("syn-a", D).partners
    assert partners["syn-e"] == OLDER
    assert partners["syn-b"] == NEWEST


def test_honest_match_after_a_far_future_match_is_flagged_late() -> None:
    # The engine never reads the clock, so a future-dated live match pins
    # the stored dates. The backend must reject such a match; when one gets
    # through, every later honest match with its players is flagged late.
    engine = RatingEngine()
    future = date(2062, 3, 1)
    engine.record(_match("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), future))
    honest = engine.record(_match("syn-m2", ("syn-a", "syn-e"), ("syn-f", "syn-g"), NEWEST))
    assert honest.counted
    assert honest.late is True
    state = engine.state("syn-a", D)
    assert state.last_played == future
    assert state.rd_as_of == future


def _seeded_engine(rng: random.Random, pool: list[str]) -> RatingEngine:
    """Seed half the pool in doubles and some in mixed, some captured mid-ledger."""
    engine = RatingEngine()
    captures = (date(2024, 6, 1), date(2025, 2, 1), date(2025, 4, 15))
    for i, pid in enumerate(pool[: len(pool) // 2]):
        captured = captures[i % len(captures)]
        engine.seed_external_rating(pid, D, _snapshot(rng.uniform(3.0, 4.5), captured))
        if i % 2 == 0:
            engine.seed_external_rating(pid, MX, _snapshot(rng.uniform(3.0, 4.5), captured))
    return engine


def _ledger(rng: random.Random, pool: list[str], count: int = 90) -> list[Match]:
    """Synthetic doubles, singles, and mixed doubles, two a day every 4 days, shuffled."""
    kinds = (D, S, MX)
    matches: list[Match] = []
    for i in range(count):
        kind = kinds[i % 3]
        size = kind.team_size
        ids = rng.sample(pool, 2 * size)
        loser = rng.randint(0, 9)
        games = ((11, loser),) if rng.random() < 0.5 else ((loser, 11),)
        on = date(2025, 1, 1) + timedelta(days=4 * (i // 2))
        matches.append(
            Match(f"syn-m{i:03d}", tuple(ids[:size]), tuple(ids[size:]), games, on, kind)
        )
    rng.shuffle(matches)
    return matches


def test_live_in_ledger_order_with_seeds_is_never_late() -> None:
    rng = random.Random(1919)
    pool = [f"syn-p{i:02d}" for i in range(12)]
    engine = _seeded_engine(rng, pool)
    results = [engine.record(match) for match in sorted(_ledger(rng, pool), key=ledger_order)]
    assert any(result.frozen for result in results)
    for result in results:
        assert result.late is False
        if result.parent is not None:
            assert result.parent.late is False
