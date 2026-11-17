"""A late live match never moves stored dates back, and is flagged (issue #19).

Live recording applies matches in arrival order. A match dated before a
player's last match in its format is late: it still counts, but
last_played, rd_as_of, and partner and opponent dates keep the newest date,
and MatchResult.late is True. All player ids are synthetic.
"""

from __future__ import annotations

import copy
import dataclasses
import math
import random
from collections.abc import Callable
from datetime import date, timedelta

import pytest

from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    MatchResult,
    RatingEngine,
    RatingFormat,
    RatingState,
    RejectionReason,
    ledger_order,
)
from open_engine.engine import reliability

D = RatingFormat.DOUBLES
S = RatingFormat.SINGLES
MX = RatingFormat.MIXED_DOUBLES
NEWEST = date(2026, 10, 1)
OLDER = date(2026, 1, 1)


def _doubles(match_id: str, team_a: tuple[str, str], team_b: tuple[str, str], on: date) -> Match:
    return Match(match_id, team_a, team_b, ((11, 7),), on, D)


def _late_engine() -> tuple[RatingEngine, MatchResult]:
    """syn-a plays on NEWEST, then a late match on OLDER with the same players plus syn-e."""
    engine = RatingEngine()
    engine.record(_doubles("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    late = engine.record(_doubles("syn-m2", ("syn-a", "syn-b"), ("syn-c", "syn-e"), OLDER))
    return engine, late


@pytest.mark.parametrize(
    ("label", "read"),
    [
        ("last_played", lambda s: s.last_played),
        ("rd_as_of", lambda s: s.rd_as_of),
        ("partner date", lambda s: s.partners["syn-b"]),
        ("opponent date", lambda s: s.opponents["syn-c"]),
    ],
)
def test_late_match_keeps_the_newest_date(
    label: str, read: Callable[[RatingState], date | None]
) -> None:
    engine, late = _late_engine()
    assert late.counted
    state = engine.state("syn-a", D)
    assert read(state) == NEWEST, label


def test_late_match_adds_a_new_opponent_with_the_late_date() -> None:
    engine, _ = _late_engine()
    state = engine.state("syn-a", D)
    assert state.opponents["syn-e"] == OLDER
    assert engine.state("syn-e", D).last_played == OLDER


def test_late_match_counts_both_matches() -> None:
    engine, _ = _late_engine()
    assert engine.state("syn-a", D).matches_played == 2


def test_rd_inflates_from_the_newest_date_after_a_late_match() -> None:
    engine, _ = _late_engine()
    cfg = engine.cfg
    state = engine.state("syn-a", D)
    later = date(2026, 12, 1)
    days = (later - NEWEST).days
    assert days == 61
    expected = round(min(cfg.rd_max, math.sqrt(state.rd**2 + cfg.rd_growth_per_day**2 * days)), 4)
    assert engine.player_summary("syn-a", D, as_of=later).rd == expected


def test_partner_seen_on_the_newest_date_still_counts_toward_variety() -> None:
    engine = RatingEngine()
    cfg = engine.cfg
    engine.record(_doubles("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    engine.record(_doubles("syn-m2", ("syn-a", "syn-b"), ("syn-c", "syn-d"), OLDER))
    # Inside the window for NEWEST, outside it for OLDER.
    on = date(2027, 3, 1)
    assert on - timedelta(days=cfg.diversity_window_days) > OLDER
    assert on - timedelta(days=cfg.diversity_window_days) <= NEWEST
    state = engine.state("syn-a", D)
    no_variety = dataclasses.replace(state, partners={}, opponents={})
    assert engine.player_summary("syn-a", D, as_of=on).reliability > reliability(
        no_variety, cfg, on
    )


def test_match_result_late_field_order() -> None:
    names = [f.name for f in dataclasses.fields(MatchResult)]
    assert "late" in names
    assert names.index("late") == names.index("imbalance") + 1
    assert names.index("parent") == names.index("late") + 1
    assert MatchResult(True, D).late is False


def test_late_match_is_flagged() -> None:
    _, late = _late_engine()
    assert late.late is True


def test_in_order_match_is_not_late() -> None:
    engine = RatingEngine()
    first = engine.record(_doubles("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), OLDER))
    second = engine.record(_doubles("syn-m2", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    assert first.late is False
    assert second.late is False


def test_same_date_match_is_not_late() -> None:
    engine = RatingEngine()
    engine.record(_doubles("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    same = engine.record(_doubles("syn-m2", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    assert same.counted
    assert same.late is False


def test_duplicate_id_is_not_late() -> None:
    engine, _ = _late_engine()
    again = engine.record(_doubles("syn-m2", ("syn-a", "syn-b"), ("syn-c", "syn-e"), OLDER))
    assert again.reason is RejectionReason.DUPLICATE_MATCH_ID
    assert again.late is False


def test_partner_gap_rejection_is_not_late_and_moves_no_date() -> None:
    engine = RatingEngine(Config(max_doubles_partner_gap=0.5))
    engine.seed_external_rating(
        "syn-h", D, ExternalRatingSnapshot("external_standard", 5.0, date(2025, 6, 1))
    )
    engine.record(_doubles("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    before = copy.deepcopy(engine.state("syn-a", D))
    rejected = engine.record(_doubles("syn-m2", ("syn-a", "syn-h"), ("syn-c", "syn-d"), OLDER))
    assert rejected.reason is RejectionReason.DOUBLES_PARTNER_GAP
    assert rejected.late is False
    assert engine.state("syn-a", D) == before


def test_before_baseline_rejection_is_not_late() -> None:
    engine = RatingEngine()
    ids = ("syn-a", "syn-b", "syn-c", "syn-d")
    for pid in ids:
        engine.seed_external_rating(
            pid, D, ExternalRatingSnapshot("external_standard", 3.5, date(2026, 6, 1))
        )
    engine.record(_doubles("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    rejected = engine.record(_doubles("syn-m2", ("syn-a", "syn-b"), ("syn-c", "syn-d"), OLDER))
    assert rejected.reason is RejectionReason.BEFORE_IMPORTED_BASELINE
    assert rejected.late is False


def test_frozen_player_with_a_later_match_makes_the_result_late() -> None:
    engine = RatingEngine()
    engine.seed_external_rating(
        "syn-a", D, ExternalRatingSnapshot("external_standard", 3.5, date(2026, 6, 1))
    )
    engine.record(_doubles("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    late = engine.record(_doubles("syn-m2", ("syn-a", "syn-w"), ("syn-x", "syn-y"), OLDER))
    assert late.counted
    assert late.frozen == ("syn-a",)
    assert late.late is True


def test_category_parent_reports_its_own_late() -> None:
    engine = RatingEngine()
    engine.record(_doubles("syn-m1", ("syn-a", "syn-b"), ("syn-c", "syn-d"), NEWEST))
    mixed = engine.record(
        Match("syn-m2", ("syn-a", "syn-w"), ("syn-x", "syn-y"), ((11, 7),), OLDER, MX)
    )
    assert mixed.counted
    assert mixed.late is True
    assert mixed.parent is not None
    assert mixed.parent.late is True
    assert engine.state("syn-a", D).last_played == NEWEST
    assert engine.state("syn-a", MX).last_played == OLDER


def _ledger(seed: int = 19, count: int = 60) -> list[Match]:
    """Synthetic doubles, singles, and mixed doubles, every 5 days, shuffled."""
    rng = random.Random(seed)
    pool = [f"syn-p{i:02d}" for i in range(12)]
    kinds = (D, S, MX)
    matches: list[Match] = []
    for i in range(count):
        kind = kinds[i % 3]
        size = kind.team_size
        ids = rng.sample(pool, 2 * size)
        loser = rng.randint(0, 9)
        games = ((11, loser),) if rng.random() < 0.5 else ((loser, 11),)
        on = date(2025, 1, 1) + timedelta(days=5 * (i // 2))
        matches.append(
            Match(f"syn-m{i:03d}", tuple(ids[:size]), tuple(ids[size:]), games, on, kind)
        )
    rng.shuffle(matches)
    return matches


def test_live_in_ledger_order_is_never_late() -> None:
    engine = RatingEngine()
    for match in sorted(_ledger(), key=ledger_order):
        result = engine.record(match)
        assert result.late is False
        if result.parent is not None:
            assert result.parent.late is False


def test_snapshot_round_trips_after_a_late_match() -> None:
    engine, _ = _late_engine()
    snapshot = engine.to_snapshot()
    rebuilt = RatingEngine.from_snapshot(snapshot)
    assert rebuilt.to_snapshot() == snapshot
    assert rebuilt.state("syn-a", D).last_played == NEWEST
