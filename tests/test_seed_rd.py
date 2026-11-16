"""An external seed's RD is always Config.external_seed_rd (issue #15)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from open_engine import Config, ExternalRatingSnapshot, RatingEngine, RatingFormat

D = RatingFormat.DOUBLES


def _snapshot(reliability: float | None) -> ExternalRatingSnapshot:
    return ExternalRatingSnapshot("external_standard", 4.0, date(2026, 10, 1), reliability)


def test_seed_external_rating_has_no_rd_override() -> None:
    seed: Any = RatingEngine().seed_external_rating
    with pytest.raises(TypeError, match="rd_override"):
        seed("a", D, _snapshot(60), rd_override=0.10)


def test_config_has_no_reliability_to_rd_field() -> None:
    make: Any = Config
    with pytest.raises(TypeError, match="external_reliability_maps_to_rd"):
        make(external_reliability_maps_to_rd=True)


@pytest.mark.parametrize("reliability", [None, 0, 60, 99, 100])
def test_seed_rd_ignores_external_reliability(reliability: float | None) -> None:
    eng = RatingEngine()
    state = eng.seed_external_rating("a", D, _snapshot(reliability))
    assert state.rd == eng.cfg.external_seed_rd
