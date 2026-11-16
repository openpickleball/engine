"""prequential_predictions rejects repeated seed ids in both modes, like add_player."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from open_engine import Match, Player, PlayerError, RatingEngine, RatingFormat
from open_engine import prequential_predictions as predict

D = RatingFormat.DOUBLES


def _seeds() -> list[Player]:
    state = RatingEngine().state("dup", D)
    return [Player("dup", {D: state}), Player("dup", {D: state})]


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_empty_ledger(mode: Any) -> None:
    with pytest.raises(PlayerError, match="^Player already exists: dup$"):
        predict([], _seeds(), mode=mode)


@pytest.mark.parametrize("mode", ["live", "scheduled"])
def test_every_window_skipped(mode: Any) -> None:
    ledger = [Match("m1", ("dup", "q"), ("r", "s"), ((11, 7),), date(2026, 10, 1), D)]
    with pytest.raises(PlayerError, match="^Player already exists: dup$"):
        predict(ledger, _seeds(), mode=mode, score_from=date(2027, 1, 1))
