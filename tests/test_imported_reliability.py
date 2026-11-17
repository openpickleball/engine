"""The headline reliability is always OPEN-earned; imported is separate (issue #15)."""

from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest

import open_engine
from open_engine import (
    Config,
    ExternalRatingSnapshot,
    Match,
    RatingEngine,
    RatingFormat,
    reliability,
)

D = RatingFormat.DOUBLES
SNAP_DATE = date(2026, 10, 1)


def _seeded(reliability_value: float | None, cfg: Config | None = None) -> RatingEngine:
    eng = RatingEngine(cfg)
    snap = ExternalRatingSnapshot("external_standard", 4.0, SNAP_DATE, reliability_value)
    eng.seed_external_rating("a", D, snap)
    return eng


def test_summary_fields_replace_basis_with_imported_reliability() -> None:
    names = [f.name for f in fields(open_engine.PlayerSummary)]
    assert "imported_reliability" in names
    assert "reliability_basis" not in names
    assert "open_reliability" not in names
    assert names.index("imported_reliability") == names.index("reliability") + 1
    assert not hasattr(open_engine, "ReliabilityBasis")


def test_carried_import_is_separate_from_headline() -> None:
    eng = _seeded(85)
    on = date(2026, 10, 2)
    summary = eng.player_summary("a", D, as_of=on)
    assert summary.reliability == reliability(eng.state("a", D), eng.cfg, on)
    assert summary.reliability < 40
    assert summary.imported_reliability == 85.0
    assert type(summary.imported_reliability) is float


def test_import_expires_with_age() -> None:
    eng = _seeded(85)
    on = date(2028, 1, 1)
    summary = eng.player_summary("a", D, as_of=on)
    assert summary.imported_reliability is None
    assert summary.reliability == reliability(eng.state("a", D), eng.cfg, on)


def test_import_expires_after_carry_matches() -> None:
    eng = _seeded(85, Config(import_reliability_carry_matches=2))
    for i in range(2):
        played = date(2026, 10, 10 + i)
        eng.record(Match(f"m{i}", ("a", f"x{i}"), (f"y{i}", f"z{i}"), ((11, 7),), played, D))
    summary = eng.player_summary("a", D, as_of=date(2026, 10, 11))
    assert summary.imported_reliability is None


@pytest.mark.parametrize("imported", [None, 0])
def test_no_import_shown_when_earned_is_not_lower(imported: float | None) -> None:
    eng = _seeded(imported)
    summary = eng.player_summary("a", D, as_of=date(2026, 10, 2))
    assert summary.imported_reliability is None
    assert summary.reliability > 0


def test_provisional_player_has_no_imported_reliability() -> None:
    eng = RatingEngine()
    eng.seed_provisional_rating("p", D)
    summary = eng.player_summary("p", D, as_of=date(2026, 10, 2))
    assert summary.imported_reliability is None
    assert summary.reliability == reliability(eng.state("p", D), eng.cfg, date(2026, 10, 2))
