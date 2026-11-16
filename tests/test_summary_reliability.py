"""Summary reliabilities are floats even when the imported value is an int."""

from __future__ import annotations

from datetime import date

from open_engine import ExternalRatingSnapshot, RatingEngine, RatingFormat


def test_imported_int_reliability_is_shown_as_float() -> None:
    eng = RatingEngine()
    snap = ExternalRatingSnapshot("ext", 4.0, date(2026, 10, 1), 85)
    eng.seed_external_rating("a", RatingFormat.DOUBLES, snap)
    summary = eng.player_summary("a", RatingFormat.DOUBLES, as_of=date(2026, 10, 2))
    assert summary.imported_reliability == 85.0
    assert type(summary.imported_reliability) is float
    assert type(summary.reliability) is float
