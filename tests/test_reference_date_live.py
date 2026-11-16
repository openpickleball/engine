"""With no as_of, a live match after the last replay moves the reference date to it (issue 17)."""

from __future__ import annotations

from datetime import date, timedelta

from open_engine import ExternalRatingSnapshot, Match, RatingFormat, replay

D = RatingFormat.DOUBLES
REPLAYED = date(2024, 3, 1)


def _ledger() -> list[Match]:
    """30 matches for syn-a, every 2 days from 2024-01-01, all partners and opponents new."""
    return [
        Match(
            f"syn-m{i}",
            ("syn-a", f"syn-p{i}"),
            (f"syn-o{i}", f"syn-q{i}"),
            ((11, 7),),
            date(2024, 1, 1) + timedelta(days=2 * i),
            D,
        )
        for i in range(30)
    ]


def test_default_summary_after_a_live_match_measures_at_that_match() -> None:
    eng = replay(_ledger(), as_of=REPLAYED)
    live_on = date(2025, 6, 1)
    eng.record(Match("syn-live", ("syn-a", "syn-r"), ("syn-s", "syn-t"), ((11, 7),), live_on, D))
    summary = eng.player_summary("syn-a", D)
    assert summary == eng.player_summary("syn-a", D, as_of=live_on)


def test_stale_import_is_not_carried_after_a_live_match() -> None:
    eng = replay([], as_of=REPLAYED)
    snap = ExternalRatingSnapshot("external_standard", 4.0, date(2024, 2, 1), 85)
    eng.seed_external_rating("syn-a", D, snap)
    live_on = date(2026, 11, 1)
    eng.record(Match("syn-live", ("syn-a", "syn-r"), ("syn-s", "syn-t"), ((11, 7),), live_on, D))
    assert eng.player_summary("syn-a", D).imported_reliability is None
    assert eng.player_summary("syn-a", D, as_of=live_on).imported_reliability is None
