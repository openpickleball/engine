"""prequential_predictions validates every seed, whatever its mode or windows."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from open_engine import (
    InvalidSeedError,
    Match,
    Player,
    RatingEngine,
    RatingFormat,
    prequential_predictions,
)

D = RatingFormat.DOUBLES


def _bad_seed() -> Player:
    partners = {f"partner{i}": date(2026, 9, 1) for i in range(20)}
    state = replace(RatingEngine().seed_provisional_rating("p", D), rd=0.10, partners=partners)
    return Player(id="p", ratings={D: state})


def test_scheduled_mode_with_empty_ledger_rejects_bad_seed() -> None:
    with pytest.raises(InvalidSeedError, match="Seed for p in doubles: partners must be empty"):
        prequential_predictions([], [_bad_seed()], mode="scheduled")


def test_scheduled_mode_with_every_window_skipped_rejects_bad_seed() -> None:
    ledger = [Match("m1", ("p", "q"), ("r", "s"), ((11, 7),), date(2026, 10, 1), D)]
    with pytest.raises(InvalidSeedError, match="Seed for p in doubles: partners must be empty"):
        prequential_predictions(
            ledger, [_bad_seed()], mode="scheduled", score_from=date(2027, 1, 1)
        )


def test_live_mode_with_empty_ledger_rejects_bad_seed() -> None:
    with pytest.raises(InvalidSeedError, match="Seed for p in doubles: partners must be empty"):
        prequential_predictions([], [_bad_seed()], mode="live")
