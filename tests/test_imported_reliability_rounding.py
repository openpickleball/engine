"""imported_reliability is shown only when its rounded value is higher than earned."""

from __future__ import annotations

from datetime import date

import pytest

from open_engine import ExternalRatingSnapshot, RatingEngine, RatingFormat

D = RatingFormat.DOUBLES
SNAP_DATE = date(2026, 10, 1)
ON = date(2026, 10, 2)


def _summary_reliabilities(imported: float) -> tuple[float, float | None]:
    eng = RatingEngine()
    eng.seed_external_rating(
        "a", D, ExternalRatingSnapshot("external_standard", 4.0, SNAP_DATE, imported)
    )
    summary = eng.player_summary("a", D, as_of=ON)
    return summary.reliability, summary.imported_reliability


@pytest.mark.parametrize("imported", [32.6, 32.64, 32.649])
def test_import_that_rounds_to_earned_is_not_shown(imported: float) -> None:
    earned, shown = _summary_reliabilities(imported)
    assert earned == 32.6
    assert shown is None


@pytest.mark.parametrize(("imported", "expected"), [(32.66, 32.7), (32.7, 32.7), (33.0, 33.0)])
def test_import_that_rounds_above_earned_is_shown(imported: float, expected: float) -> None:
    earned, shown = _summary_reliabilities(imported)
    assert earned == 32.6
    assert shown == expected
