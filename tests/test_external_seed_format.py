"""seed_external_rating rejects a rating format that is not a RatingFormat."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from open_engine import ExternalRatingSnapshot, InvalidSeedError, RatingEngine, RatingFormat

SNAP = ExternalRatingSnapshot("external_standard", 4.0, date(2026, 10, 1), 85, "ext-1")


@pytest.mark.parametrize("rating_format", ["doubles", "tennis", None, 1])
def test_rating_format_must_be_a_rating_format(rating_format: Any) -> None:
    eng = RatingEngine()
    with pytest.raises(InvalidSeedError, match="rating_format must be a RatingFormat"):
        eng.seed_external_rating("a", rating_format, SNAP)
    assert eng.players == {}


def test_rating_format_member_is_accepted() -> None:
    eng = RatingEngine()
    eng.seed_external_rating("a", RatingFormat.DOUBLES, SNAP)
    assert list(eng.players["a"].ratings) == [RatingFormat.DOUBLES]
    assert type(next(iter(eng.players["a"].ratings))) is RatingFormat
